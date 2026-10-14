"""check-vars.py must not treat contents of Jinja {% raw %} blocks as var refs."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-vars.py"


def run_check(tmp_path: Path, tasks_yml: str) -> subprocess.CompletedProcess:
    tasks = tmp_path / "ansible" / "roles" / "r" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text(tasks_yml)
    (tmp_path / "molecule").mkdir()
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path)],
        capture_output=True, text=True,
    )


def test_ref_inside_raw_is_ignored(tmp_path):
    res = run_check(tmp_path, (
        "- name: go template\n"
        "  command: >-\n"
        "    docker inspect --format\n"
        "    '{% raw %}{{.Name}} {{index .Config.Labels \"x\"}}{% endraw %}'\n"
        "- name: multiline trimmed\n"
        "  command: >-\n"
        "    echo {%- raw -%}\n"
        "    {{ multi_line_undefined }}\n"
        "    {%- endraw -%}\n"
    ))
    assert res.returncode == 0, res.stderr


def test_undefined_ref_outside_raw_still_reported(tmp_path):
    res = run_check(tmp_path, (
        "- name: mixed\n"
        "  command: >-\n"
        "    echo {% raw %}{{index .X}}{% endraw %} {{ really_undefined }}\n"
    ))
    assert res.returncode == 1
    assert "really_undefined" in res.stderr
    assert "index" not in res.stderr.replace("undefined references", "")
