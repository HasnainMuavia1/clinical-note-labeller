import pytest

from app.workspace.filetools import FileOp, GuardedFileTools
from app.workspace.paths import PathEscapeError


@pytest.fixture()
def tools(tmp_path):
    events = []
    root = tmp_path / "root"
    root.mkdir()
    return GuardedFileTools(root, lambda a, d: events.append((a, d))), root, events


def test_plan_copy_returns_a_copy_op_when_target_is_free(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("x")
    op = t.plan_copy(src, "output/with-codes/Cardiology/in.pdf", "coded cardiology note")
    assert op.op == "copy"


def test_plan_copy_returns_an_overwrite_op_when_target_exists(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("x")
    target = root / "output" / "a.pdf"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    op = t.plan_copy(src, "output/a.pdf", "collision")
    assert op.op == "overwrite"


def test_copy_executes_and_creates_parent_directories(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("hello")
    op = t.plan_copy(src, "output/without-codes/Neurology/in.pdf", "r")
    result = t.execute(op)
    assert result.read_text() == "hello"


def test_overwrite_without_approval_is_refused(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("new")
    (root / "out.pdf").write_text("old")
    op = t.plan_copy(src, "out.pdf", "collision")
    with pytest.raises(PermissionError):
        t.execute(op)
    assert (root / "out.pdf").read_text() == "old"


def test_overwrite_with_approval_proceeds(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("new")
    (root / "out.pdf").write_text("old")
    op = t.plan_copy(src, "out.pdf", "collision")
    t.execute(op, approved=True)
    assert (root / "out.pdf").read_text() == "new"


def test_delete_without_approval_is_refused(tools):
    t, root, _ = tools
    victim = root / "victim.pdf"
    victim.write_text("x")
    with pytest.raises(PermissionError):
        t.execute(FileOp("delete", None, "victim.pdf", "cleanup"))
    assert victim.exists()


def test_escape_is_denied_and_audited(tools):
    t, root, events = tools
    with pytest.raises(PathEscapeError):
        t.execute(FileOp("copy", str(root / "in.pdf"), "../escaped.pdf", "bad"))
    assert any(action == "path_escape_denied" for action, _ in events)


def test_unique_target_suffixes_on_collision(tools):
    t, root, _ = tools
    (root / "a.pdf").write_text("x")
    assert t.unique_target("a.pdf") == "a__2.pdf"


def test_toolkit_is_scoped_to_the_root(tools):
    t, root, _ = tools
    toolkit = t.toolkit()
    assert str(root) in str(toolkit.root_dir)
    assert {tool.name for tool in toolkit.get_tools()} >= {"copy_file", "list_directory"}
