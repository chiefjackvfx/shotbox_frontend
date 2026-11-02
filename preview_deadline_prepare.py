"""Prepare a standalone preview scene inside Nuke without rendering."""
import json
from pathlib import Path
import sys


def main():
    import nuke
    from nuke_headless_tasks import _PreviewTemplateEditor, get_preview_template_path
    request = json.loads(Path(sys.argv[-1]).read_text(encoding='utf-8'))
    first, last = _PreviewTemplateEditor(nuke).render(
        template_path=str(get_preview_template_path()), **request['arguments'],
        prepare_scene=request['scene'])
    Path(request['result']).write_text(json.dumps({
        'first': first, 'last': last, 'nuke_version': nuke.NUKE_VERSION_STRING,
    }), encoding='utf-8')


if __name__ == '__main__':
    main()
