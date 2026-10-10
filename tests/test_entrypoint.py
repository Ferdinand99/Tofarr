from app.entrypoint import prepare


class Ops:
    def __init__(self, fail_chown=False):
        self.calls, self.fail_chown = [], fail_chown

    def makedirs(self, path): self.calls.append(("makedirs", path))

    def chown_tree(self, path, uid, gid):
        if self.fail_chown:
            raise PermissionError("not permitted")
        self.calls.append(("chown", path, uid, gid))

    def drop(self, uid, gid): self.calls.append(("drop", uid, gid))


def test_root_hands_the_config_folder_to_the_unraid_user_and_drops_to_it():
    ops = Ops()
    assert prepare({"CONFIG_DIR": "/config"}, 0, ops) == (99, 100)
    assert ops.calls == [("makedirs", "/config"), ("chown", "/config", 99, 100), ("drop", 99, 100)]


def test_puid_and_pgid_can_be_changed():
    ops = Ops()
    assert prepare({"CONFIG_DIR": "/config", "PUID": "1000", "PGID": "1001"}, 0, ops) == (1000, 1001)
    assert ("drop", 1000, 1001) in ops.calls


def test_puid_zero_keeps_running_as_root():
    ops = Ops()
    assert prepare({"CONFIG_DIR": "/config", "PUID": "0", "PGID": "0"}, 0, ops) is None
    assert not any(c[0] == "drop" for c in ops.calls)


def test_bad_values_fall_back_to_the_defaults():
    ops = Ops()
    assert prepare({"CONFIG_DIR": "/config", "PUID": "abc", "PGID": "-5"}, 0, ops) == (99, 100)


def test_a_container_started_as_a_normal_user_is_left_alone():
    ops = Ops()
    assert prepare({"CONFIG_DIR": "/config"}, 1000, ops) is None
    assert ops.calls == []


def test_a_chown_that_is_refused_does_not_stop_the_app_from_starting():
    ops = Ops(fail_chown=True)          # for example a Windows bind mount that does not support it
    assert prepare({"CONFIG_DIR": "/config"}, 0, ops) == (99, 100)
    assert ("drop", 99, 100) in ops.calls
