# Matchmove assets

Job Assets → Matchmove offers Create New 3DE Project. Both click and
right-click open the assets menu. Creation offers valid EXR precomp sequences
from every timeline in the job, labelled by timeline and shot. The project name
defaults to the job title.

Each job matchmove project name gets its own filename button beside Job Assets,
showing its latest numbered version. There is no project-count limit or chooser;
buttons wrap onto more rows when needed. Each opens its project directly and
omits `_matchmove` from its label. Right-click any project button for Open Work
in Explorer and Open Export in Explorer. Buttons refresh with filesystem
checks, job changes and project creation, and disappear when their projects no
longer exist. Existing projects are not listed in the Job Assets menu.

3DE project buttons use purple charcoal, pale lavender text, and a purple left edge
with distinct hover, keyboard-focus, pressed, and disabled states. This
text-only treatment shares ShotBox's typography and sizing across job and shot
buttons, including grid cards, and legacy timeline matchmove access. It uses
no icons or emoji. Purple distinguishes project buttons from the green render
conform indicators and ShotBox’s orange general actions.

New job projects use `VFX/job_assets/matchmove/work` and
`VFX/job_assets/matchmove/export`, or the job’s saved `matchmove_path`.
Existing `Job_Assets` folders remain supported.

Shot cards show a separate matchmove button between Assets and Precomp when an
existing project is found. The button shows the latest 3DE filename with
`_matchmove` omitted from its label. Filenames on disk and tooltip paths remain
unchanged. For several project names it shows the first filename with a count
of additional projects.
Left-click opens the project directly when there is one project name, or opens
a chooser when there are several. Right-click offers Open Work in Explorer and
Open Export in Explorer, opening the project’s work or export folder in the
system file browser. Detection refreshes with shot data and filesystem updates,
and after project creation. The button
stays hidden in compact mode and disappears when no projects remain.

Assets opens the shot assets folder. Its right-click menu offers Create
Matchmove from valid precomp sequences, including when the shot already has a
project. Hover a sequence to view the same EXR preview used by Precomp, then
choose Create Matchmove in its submenu. Creating another project saves the next
numbered version in the shot’s resolved matchmove directory, including a saved
custom path. Existing projects
are opened through the filename button rather than the Assets menu.

The shot/job filename choosers and legacy timeline menu show one highest numbered
version per project name and keep standalone `.3de` files visible. Earlier
versions remain on disk and count toward the next version number.

New imports no longer create `timeline_assets`. Timeline Assets appears only
for a timeline with an existing `timeline_assets` or `Timeline_Assets` folder
on disk. A saved matchmove path alone does not make the button appear.
It opens existing folders and projects; new multi-clip projects are created
through Job Assets. Legacy files and shot/timeline database paths stay in place.

## Deployment

Deploy the updated backend first. From `shotbox_backend`, using its Python
environment, run:

```sh
python manage.py migrate
```

Migration `0035_job_matchmove_path` adds the nullable job field without changing
existing shot or timeline records. The job endpoint now supports PATCH.
Deploy the updated frontend after applying the migration. This frontend requires
the updated backend for saving job matchmove paths.
