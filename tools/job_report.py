#!/usr/bin/env python3
"""Read-only SQLite job report. Install tools/report-requirements.txt first."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from html import escape
import json
from io import BytesIO
from pathlib import Path
import sqlite3
import re
from PIL import Image as PillowImage
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.graphics.shapes import Drawing, Rect, String, Circle, Line, PolyLine
from reportlab.graphics.charts.piecharts import Pie
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                               TableStyle, PageBreak, KeepTogether, Image, CondPageBreak)

INK = colors.HexColor('#253634')
MUTED = colors.HexColor('#64736E')
PALETTE = ['#426C63', '#B58B64', '#91AAA0', '#C6CFC7', '#8E929A', '#D9C9B7', '#6E8496', '#A790A5', '#B3AC77', '#8C776A']
PRODUCTION = {'status_change', 'conform_updated', 'render_updated',
              'task_created', 'task_deleted', 'artist_assigned'}
STYLES = {
    'title': ParagraphStyle('title', fontName='Times-Roman', fontSize=38, leading=43, textColor=INK, spaceAfter=16),
    'h': ParagraphStyle('h', fontName='Helvetica', fontSize=20, leading=25, textColor=INK, spaceAfter=12, keepWithNext=True),
    'sub': ParagraphStyle('sub', fontName='Helvetica-Bold', fontSize=11, leading=15, textColor=INK, spaceAfter=7, keepWithNext=True),
    'body': ParagraphStyle('body', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, spaceAfter=7),
    'small': ParagraphStyle('small', fontName='Helvetica', fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=5),
    'metric': ParagraphStyle('metric', fontName='Helvetica', fontSize=23, leading=28, textColor=INK, alignment=TA_CENTER),
}


class ReportDocument(SimpleDocTemplate):
    """Track sections while laying out, then draw a truthful running header."""
    section = 'Production review'
    outline_index = 0

    def beforePage(self):
        self.page_section = self.section
        self.page_section_set = False

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == 'h':
            self.section = flowable.getPlainText()
            self.outline_index += 1
            key = f'section-{self.outline_index}'
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(self.section, key, level=0)
            if not self.page_section_set:
                self.page_section = self.section
                self.page_section_set = True
        elif hasattr(flowable, 'report_section') and not self.page_section_set:
            self.page_section = flowable.report_section
            self.page_section_set = True

    def afterPage(self):
        if self.page > 1:
            self.canv.saveState()
            self.canv.setFont('Helvetica', 7)
            self.canv.setFillColor(MUTED)
            self.canv.drawString(50, 813, self.page_section.upper())
            self.canv.setStrokeColor(colors.HexColor('#D8DED8'))
            self.canv.line(50, 804, 545, 804)
            self.canv.restoreState()


def p(text, style='body'):
    return Paragraph(escape(str(text)).replace('\n', '<br/>'), STYLES[style])


def date(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo('Europe/London')).date()


def load(db, job_name):
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute('BEGIN')  # One consistent read snapshot; never modify the database.
        jobs = [dict(r) for r in con.execute('SELECT id,title FROM tasks_job')]
        matches = [j for j in jobs if str(j['id']) == job_name or j['title'].casefold() == job_name.casefold()]
        if not matches:
            matches = [j for j in jobs if job_name.casefold().replace(' ', '_') in j['title'].casefold()]
        if len(matches) != 1:
            raise ValueError(f'Expected one matching job, found {len(matches)}: {matches}')
        job = matches[0]
        timelines = [dict(r) for r in con.execute('SELECT id,title FROM tasks_timeline WHERE job_id=? ORDER BY id', (job['id'],))]
        shots = [dict(r) for r in con.execute('SELECT s.* FROM tasks_shot s JOIN tasks_timeline l ON l.id=s.timeline_id WHERE l.job_id=? ORDER BY s.title,s.id', (job['id'],))]
        tasks = [dict(r) for r in con.execute('SELECT t.* FROM tasks_task t JOIN tasks_shot s ON s.id=t.shot_id JOIN tasks_timeline l ON l.id=s.timeline_id WHERE l.job_id=? ORDER BY t.id', (job['id'],))]
        users = {r['id']: r['username'] for r in con.execute('SELECT id,username FROM auth_user')}
        shot_ids = {s['id'] for s in shots}
        task_shots = {t['id']: t['shot_id'] for t in tasks}
        events = []
        for row in con.execute('SELECT * FROM tasks_activitylog ORDER BY timestamp,id'):
            event = dict(row)
            try:
                details = json.loads(event['details'] or '{}')
            except (ValueError, TypeError):
                details = {}
            sid = event['shot_id'] or task_shots.get(event['task_id'])
            if sid in shot_ids or str(details.get('job_id')) == str(job['id']):
                event['details'] = details
                event['resolved_shot'] = sid
                events.append(event)
    return job, timelines, shots, tasks, users, events


def picture(path, width, height):
    try:
        with PillowImage.open(path) as source:
            source.thumbnail((int(width * 2.5), int(height * 2.5)))
            converted = source.convert('RGB')
            w, h = converted.size
            data = BytesIO()
            converted.save(data, format='JPEG', quality=88, optimize=True)
            data.seek(0)
        scale = min(width / w, height / h)
        return Image(data, width=w * scale, height=h * scale)
    except Exception:
        return p('Thumbnail unavailable', 'small')


def bars(counts, width=495):
    rows = list(counts.items())
    d = Drawing(width, max(35, len(rows)*27))
    maximum = max(counts.values(), default=1) or 1
    for i, (label, count) in enumerate(rows):
        y = d.height - (i+1)*27 + 6
        d.add(String(0, y+3, str(label), fontName='Helvetica', fontSize=8, fillColor=INK))
        d.add(Rect(165, y, (width-205)*count/maximum, 13, fillColor=INK, strokeColor=None))
        d.add(String(170+(width-205)*count/maximum, y+3, str(count), fontSize=8, fillColor=MUTED))
    return d


def donut(counts):
    nonzero = [(k,v) for k,v in counts.items() if v]
    height = max(150, 32 + 22 * len(nonzero))
    d = Drawing(495, height)
    top_offset = height - 150
    if not nonzero:
        d.add(String(0, 70, 'No recorded production activity', fontSize=10))
        return d
    pie = Pie()
    pie.x, pie.y, pie.width, pie.height = 5, 5 + top_offset, 140, 140
    pie.data = [v for _,v in nonzero]
    pie.labels = None
    for i, (label, count) in enumerate(nonzero):
        col = colors.HexColor(PALETTE[i % len(PALETTE)])
        pie.slices[i].fillColor = col
        pie.slices[i].strokeColor = colors.white
        y = height - 27 - i*22
        d.add(Rect(180,y,8,8,fillColor=col,strokeColor=None))
        d.add(String(195,y, f'{label}: {count} ({count/sum(pie.data):.0%})',fontSize=8,fillColor=INK))
    d.add(pie)
    d.add(Circle(75,75 + top_offset,39,fillColor=colors.HexColor('#FAFAF7'),strokeColor=None))
    d.add(String(75,72 + top_offset,str(sum(pie.data)),fontSize=19,textAnchor='middle',fillColor=INK))
    return d


def grid(rows, widths):
    table = Table([[p(cell,'small') for cell in row] for row in rows], colWidths=widths, hAlign='LEFT')
    table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,0),.5,colors.HexColor('#CCD5CE'))]))
    return table


def plural(number, word):
    return f"{number} {word}{'' if number == 1 else 's'}"


def task_summary(tasks):
    if not tasks:
        return 'No task-level work description is recorded.'
    counts = Counter(t['status'] for t in tasks)
    parts = []
    for status, label in [('approved', 'approved'), ('done', 'done'),
                          ('waiting_for_approval', 'awaiting approval')]:
        if counts[status]:
            parts.append(f"{plural(counts[status], 'task')} {label}")
    other = sum(n for status, n in counts.items()
                if status not in ('approved', 'done', 'waiting_for_approval'))
    if other:
        parts.append(f"{plural(other, 'task')} in other open states")
    return '; '.join(parts) + '.'


def work_summary(tasks):
    """Describe only the recorded scope; ambiguous titles stay quoted."""
    if not tasks:
        return 'No task-level work description is recorded.'
    titles = list(dict.fromkeys(t['title'] for t in tasks))
    quoted = [f'“{title}”' for title in titles]
    scope = ', '.join(quoted[:-1]) + ' and ' + quoted[-1] if len(quoted)>1 else quoted[0]
    return 'Recorded work: ' + scope + '.'


def approval_events(events):
    return [e for e in events if e['action_type'] == 'status_change'
            and (e['new_value'] or '').lower() in ('approved', 'done')]


def display_title(title):
    return title.replace('_', ' ')


COMPLETED = {'approved', 'done'}


def owner_groups(tasks, users):
    owners = {t['artist_id'] for t in tasks}
    return [(uid, users.get(uid, 'Unassigned'),
             [t for t in tasks if t['artist_id'] == uid])
            for uid in sorted(owners, key=lambda uid: (uid is None, users.get(uid, '').casefold()))]


def owner_chart(tasks, users):
    groups = owner_groups(tasks, users)
    d = Drawing(495, 40 + len(groups)*36)
    maximum = max((len(ts) for _,_,ts in groups), default=1)
    for i, (_,name,ts) in enumerate(groups):
        y = d.height - 40 - i*36
        done = sum(t['status'] in COMPLETED for t in ts)
        active = len(ts)-done
        d.add(String(0,y+4,name,fontSize=9,fillColor=INK))
        x=115
        for count,col in [(done,PALETTE[0]),(active,PALETTE[1])]:
            w=300*count/maximum
            if count:
                d.add(Rect(x,y,w,17,fillColor=colors.HexColor(col),strokeColor=None))
            x+=w
        d.add(String(x+7,y+4,f'{done} / {active}',fontSize=8,fillColor=MUTED))
    for x,label,col in [(0,'Completed',PALETTE[0]),(130,'Active',PALETTE[1])]:
        d.add(Rect(x,5,8,8,fillColor=colors.HexColor(col),strokeColor=None))
        d.add(String(x+14,5,label,fontSize=8,fillColor=MUTED))
    return d


def metric_tiles(tasks):
    status = Counter(t['status'] for t in tasks)
    active = [t for t in tasks if t['status'] not in COMPLETED]
    metrics = [(len(active),'ACTIVE TASKS'),
               (len(tasks)-len(active),'COMPLETED'),
               (sum(t['artist_id'] is None for t in tasks),'NO ASSIGNED ARTIST'),
               (status['in_progress'],'IN PROGRESS'),
               (status['waiting_for_approval'],'AWAITING APPROVAL'),
               (len({t['artist_id'] for t in active if t['artist_id'] is not None}),'ACTIVE ARTISTS')]
    rows=[]
    for offset in (0,3):
        rows.append([[p(value,'metric'),p(label,'small')] for value,label in metrics[offset:offset+3]])
    table=Table(rows,colWidths=[165]*3)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#F0F3EE')),
                              ('BOX',(0,0),(-1,-1),.5,colors.HexColor('#D8DED8')),
                              ('INNERGRID',(0,0),(-1,-1),4,colors.HexColor('#FAFAF7')),
                              ('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    return table


def producer_pages(tasks,shots,timelines,users,media):
    story=[p('Producer overview','h'),
           p('Who holds the work, what is complete, and what still needs attention.'),
           p('All timelines · includes hidden shots and tasks. Counts may differ from the web overview with hidden items switched off.','small'),
           metric_tiles(tasks),Spacer(1,20),p('Work by artist','sub'),owner_chart(tasks,users),
           p('Bars show current assigned tasks. Labels give completed / active counts; completed means Approved or Done.','small'),Spacer(1,14),
           p('Current task status','sub'),donut(Counter(t['status'].replace('_',' ').capitalize() for t in tasks)),
           p('Tasks without an artist may already be approved. Assignment describes recorded responsibility, not proof of authorship.','small'),PageBreak()]
    shot_map={s['id']:s for s in shots}
    timeline_names={tl['id']:display_title(tl['title']) for tl in timelines}
    columns=[]
    for uid,name,owned in owner_groups(tasks,users):
        records=[]
        for completed in (False,True):
            subset=[t for t in owned if (t['status'] in COMPLETED)==completed]
            for sid in sorted({t['shot_id'] for t in subset},key=lambda sid:shot_map[sid]['title']):
                records.append((completed,shot_map[sid],[t for t in subset if t['shot_id']==sid]))
        for start in range(0,len(records),4):
            done=sum(t['status'] in COMPLETED for t in owned)
            cells=[p(name + (' · continued' if start else ''),'sub'),
                   p(f'{plural(len(owned), "task")} · {len(owned)-done} active · {done} completed','small')]
            for completed,shot,shot_tasks in records[start:start+4]:
                heading=Table([[picture(media/(shot['thumbnail'] or ''),62,42),
                    [p(shot['title'],'sub'),p(timeline_names[shot['timeline_id']],'small')]]],colWidths=[72,146])
                heading.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0)]))
                label='COMPLETED' if completed else 'ACTIVE / NEEDS ATTENTION'
                if shot['hidden']: label+=' · HIDDEN SHOT'
                cells += [Spacer(1,7),p(label,'small'),heading]
                for t in shot_tasks:
                    title=t['title'] + (f' — {t["notes"]}' if t['notes'] else '')
                    if t['hidden']: title+=' [hidden task]'
                    cells.append(p(title+' · '+t['status'].replace('_',' '),'small'))
            columns.append(cells)
    for start in range(0,len(columns),2):
        story += [p('Artist workboard' + (' · continued' if start else ''),'h'),
                  p('Current tasks grouped by artist, then by active or completed work. A shared shot appears under each assigned artist.','small')]
        cells=columns[start:start+2]
        if len(cells)==1:cells.append([])
        board=Table([[cells[0],'',cells[1]]],colWidths=[239,17,239])
        board.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),
           ('BACKGROUND',(0,0),(0,0),colors.white),('BACKGROUND',(2,0),(2,0),colors.white),
           ('BOX',(0,0),(0,0),.5,colors.HexColor('#D8DED8')),('BOX',(2,0),(2,0),.5,colors.HexColor('#D8DED8')),
           ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
           ('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
        story += [board,PageBreak()]
    return story


def activity_calendar(events):
    counts=Counter(date(e['timestamp']) for e in events)
    if not counts:return bars({})
    first=min(counts)-timedelta(days=min(counts).weekday())
    last=max(counts)
    weeks=(last-first).days//7+1
    d=Drawing(495,40+weeks*33)
    maximum=max(counts.values())
    for day,label in enumerate(['Mon','Tue','Wed','Thu','Fri','Sat','Sun']):
        d.add(String(105+day*50,d.height-12,label,fontSize=8,fillColor=MUTED))
    for week in range(weeks):
        start=first+timedelta(weeks=week);y=d.height-48-week*33
        d.add(String(0,y+8,start.strftime('%d %b'),fontSize=8,fillColor=MUTED))
        for day in range(7):
            current=start+timedelta(days=day);n=counts[current]
            shade=colors.linearlyInterpolatedColor(colors.HexColor('#EAF0EB'),INK,0,maximum,n)
            d.add(Rect(100+day*50,y,42,26,fillColor=shade,strokeColor=None))
            d.add(String(121+day*50,y+9,str(n) if current<=last else '–',textAnchor='middle',fontSize=8,fillColor=colors.white if n>maximum/2 else INK))
    return d


def approval_curve(events):
    production=[e for e in events if e['action_type'] in PRODUCTION]
    if not production:return bars({})
    daily=Counter(date(e['timestamp']) for e in approval_events(events))
    first=min(date(e['timestamp']) for e in production)
    last=max(date(e['timestamp']) for e in production)
    span=max((last-first).days,1)
    maximum=max(sum(daily.values()),1)
    d=Drawing(495,160)
    for value in sorted({0,maximum//2,maximum}):
        y=30+value/maximum*105
        d.add(Line(35,y,470,y,strokeColor=colors.HexColor('#D8DED8'),strokeWidth=.5))
        d.add(String(25,y-3,str(value),textAnchor='end',fontSize=8,fillColor=MUTED))
    points=[];total=0
    for i in range(span+1):
        total+=daily[first+timedelta(days=i)]
        points += [35+i/span*435,30+total/maximum*105]
    d.add(PolyLine(points,strokeColor=INK,strokeWidth=2))
    for i in sorted({0,span//2,span}):
        d.add(String(35+i/span*435,10,(first+timedelta(days=i)).strftime('%d %b'),textAnchor='middle',fontSize=8,fillColor=MUTED))
    d.add(Circle(470,30+total/maximum*105,3,fillColor=INK,strokeColor=None))
    return d


def history_charts(events):
    production=[e for e in events if e['action_type'] in PRODUCTION]
    return [p('Production rhythm','h'),p('Daily activity, approval milestones and the mix of recorded changes.'),
            p('Production activity calendar','sub'),activity_calendar(production),
            p('Cell values are event counts. Darker cells mean more recorded changes; zero means no logged production events.','small'),Spacer(1,12),
            p('Cumulative approval / completion events','sub'),approval_curve(events),
            p('Historical transitions into Approved or Done, including repeated approvals and deleted tasks. This is not the count of currently completed tasks.','small'),Spacer(1,12),
            p('Production event mix','sub'),donut(Counter(e['action_type'].replace('_',' ').capitalize() for e in production)),PageBreak()]


def generate(args):
    job, timelines, shots, tasks, users, events = load(args.db, args.job)
    production = [e for e in events if e['action_type'] in PRODUCTION]
    generated = datetime.now(ZoneInfo('Europe/London'))
    story = []
    story += [p('SHOTBOX  /  PRODUCTION REVIEW', 'small'), Spacer(1,16), p(re.sub(r'^C\d+[_ ]+', '', job['title']).replace('_',' '),'title'), p('Activity, people & shot history','sub')]
    candidates = [s for s in shots if not s['hidden'] and s['thumbnail']
                  and any(t['shot_id']==s['id'] for t in tasks)]
    hero = next(iter(candidates), next((s for s in shots if s['thumbnail']), None))
    for candidate in candidates:
        try:
            with PillowImage.open(args.media / candidate['thumbnail']) as source:
                if source.width / source.height >= 1.3:
                    hero = candidate
                    break
        except (OSError, ValueError):
            continue
    if hero:
        story += [picture(args.media/hero['thumbnail'],495,280), Spacer(1,18)]
    metrics = Table([[p(len(timelines),'metric'),p(len(shots),'metric'),p(len(production),'metric')], [p('TIMELINES','small'),p('SHOTS · INCLUDING HIDDEN','small'),p('PRODUCTION EVENTS','small')]],colWidths=[165]*3)
    story += [metrics,Spacer(1,18),p(f"This report covers {len(shots)} shots across {len(timelines)} timelines, including {sum(s['hidden'] for s in shots)} hidden shots. It follows task assignments, status changes and recorded conform/render updates."), p(task_summary(tasks)), p('Inside: producer overview · artist workboard · activity charts · shot records','small')]
    if events:
        story += [p(f'Recorded activity: {date(events[0]["timestamp"]):%d %b %Y} – {date(events[-1]["timestamp"]):%d %b %Y}.','small')]
    story += [p(f'Generated {generated:%d %b %Y, %H:%M %Z}.','small'),PageBreak()]
    story += producer_pages(tasks, shots, timelines, users, args.media)
    story += [p('Activity overview','h'),p('Charts count recorded changes, not working hours or effort. All timelines and hidden shots are included.')]
    weekly = Counter()
    for e in production:
        day = date(e['timestamp']); weekly[day-timedelta(days=day.weekday())] += 1
    if weekly:
        week = min(weekly)
        while week <= max(weekly):
            weekly.setdefault(week,0); week += timedelta(days=7)
    story += [p('Production events by week','sub'),bars({k.strftime('%d %b'):v for k,v in sorted(weekly.items())}),Spacer(1,15)]
    shot_timeline = {s['id']:s['timeline_id'] for s in shots}
    timeline_names = {t['id']:t['title'] for t in timelines}
    counts = Counter(timeline_names.get(shot_timeline.get(e['resolved_shot']),e['details'].get('timeline_title','Unknown timeline')) for e in production)
    shot_names = {s['id']: s['title'] for s in shots}
    busiest = Counter(shot_names.get(e['resolved_shot'], e['details'].get('shot_title','Unknown shot')) for e in production)
    story += [p('Where activity happened','sub'),donut({display_title(k):v for k,v in counts.items()}),Spacer(1,12),p('Shots with the most recorded changes','sub'),bars(dict(busiest.most_common(5)))]
    story += [p('Included: task creation/deletion, assignments, status changes, conform and render updates. Excluded from charts: notes, colour, duration, shot creation and visibility edits. A conform update is not necessarily a new render or client revision.','small'),PageBreak()]
    story += history_charts(events)
    story += [p('People & record quality','h'),p('Recorded actions by author','sub'),bars(Counter(users.get(e['user_id'],'Unattributed') for e in production)),Spacer(1,15)]
    story += [p('Current task assignments','sub'),grid([['Artist','Assigned tasks','Approved / done']] + [[name,str(sum(t['artist_id']==uid for t in tasks)),str(sum(t['artist_id']==uid and t['status'] in ('approved','done') for t in tasks))] for uid,name in sorted({t['artist_id']:users.get(t['artist_id'],'Unassigned') for t in tasks}.items(),key=lambda item:item[1])],[245,125,125])]
    outstanding = [t for t in tasks if t['status'] not in ('approved','done')]
    open_description = '; '.join(
        f"{shot_names.get(t['shot_id'], 'Unknown shot')} — {t['title']} "
        f"({users.get(t['artist_id'], 'Unassigned')}, {t['status'].replace('_', ' ')})"
        for t in outstanding)
    story += [Spacer(1,12),p('Approval record','sub'),p(task_summary(tasks)),p('Outstanding: ' + (open_description or 'No open current tasks.')),p(f'{len(approval_events(events))} recorded transitions into Approved or Done. These are historical events, including repeated approvals and tasks subsequently deleted.', 'small'),p('How to read this report','sub'),p(f"{sum(e['user_id'] is None for e in events)} of {len(events)} total activity records have no recorded author. Assignment history names the assigned artist; it does not identify who performed an action. Neither chart is a productivity ranking."),p('Shot cards show current tasks and recorded historical assignments. Hidden tasks are labelled. Deleted tasks may still contribute activity but are absent from the current task list.'),p('Activity spans run from the first to last recorded event on each shot and can include later administrative edits. They do not measure hours worked. No work-time estimates are made.'),p('Task titles and notes are kept literal. Approval applies to individual tasks; a shot without tasks is not assumed approved. Latest conform filenames are recorded references, not verified delivery files.'),PageBreak()]
    for ti,timeline in enumerate(timelines):
        group = [s for s in shots if s['timeline_id']==timeline['id']]
        timeline_tasks = [t for t in tasks if any(s['id']==t['shot_id'] for s in group)]
        story += [CondPageBreak(260), p(display_title(timeline['title']), 'h'), p(f'{len(group)} shot' + ('s' if len(group)!=1 else '') + f' · {sum(s["hidden"] for s in group)} hidden · {counts.get(timeline["title"],0)} production events','small'), p(task_summary(timeline_tasks)), Spacer(1,8)]
        for shot in group:
            ts = [t for t in tasks if t['shot_id']==shot['id']]
            es = [e for e in events if e['resolved_shot']==shot['id']]
            title = shot['title'] + ('  /  HIDDEN' if shot['hidden'] else '')
            info = [p(title,'sub'), p(work_summary(ts), 'body')]
            if ts:
                info.append(grid([['Task / note', 'Assigned to', 'Status']] + [
                    [t['title'] + (f" — {t['notes']}" if t['notes'] else '') +
                     (' [hidden]' if t['hidden'] else ''),
                     users.get(t['artist_id'], 'Unassigned'),
                     t['status'].replace('_', ' ')] for t in ts], [133, 65, 78]))
            assignments = sorted({e['new_value'] for e in es if e['action_type']=='artist_assigned' and e['new_value'] not in (None,'None','Unassigned','')})
            if assignments:
                info += [p('Historical assignments: ' + ', '.join(assignments),'small')]
            approvals = approval_events(es)
            if approvals:
                info += [p(f'Latest recorded approval / done: {date(approvals[-1]["timestamp"]):%d %b %Y}', 'small')]
            if es:
                first,last=date(es[0]['timestamp']),date(es[-1]['timestamp'])
                info += [p(f'{len(es)} recorded events · {plural(len({date(e["timestamp"]) for e in es}), "active date")}\n{first:%d %b} – {last:%d %b %Y} · {(last-first).days} elapsed day{"s" if (last-first).days != 1 else ""}','small')]
            conform = shot.get('last_conform')
            if conform and conform != 'None':
                info += [p('Latest conform: '+conform,'small')]
            card = Table([[picture(args.media/(shot['thumbnail'] or ''),190,130),info]],colWidths=[210,285], minRowHeights=[155])
            card.report_section = display_title(timeline['title'])
            card.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),12),('LINEBELOW',(0,0),(-1,-1),.5,colors.HexColor('#D8DED8'))]))
            story += [KeepTogether([card,Spacer(1,14)])]
    if isinstance(story[-1],PageBreak): story.pop()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def footer(canvas,doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor('#FAFAF7')); canvas.rect(0,0,595.28,841.89,fill=1,stroke=0)
        canvas.setFillColor(MUTED); canvas.setFont('Helvetica',7)
        canvas.drawString(50,30,job['title']+'  /  INTERNAL PRODUCTION REVIEW')
        canvas.drawRightString(545,30,f'{doc.page:02d}')
        canvas.restoreState()
    doc=ReportDocument(str(args.output),pagesize=(595.28,841.89),rightMargin=50,leftMargin=50,topMargin=48,bottomMargin=50,title=job['title']+' — Production Report',author='Shotbox')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    print(json.dumps({'pdf':str(args.output),'shots':len(shots),'timelines':len(timelines),'all_events':len(events),'production_events':len(production)},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True,help='Read-only SQLite snapshot for this standalone prototype')
    parser.add_argument('--media',type=Path,required=True,help='Local thumbnail media directory')
    parser.add_argument('--job',required=True,help='Job ID, exact title or unique title fragment')
    parser.add_argument('--output',type=Path,required=True)
    generate(parser.parse_args())
