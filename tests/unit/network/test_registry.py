from network.tcp.registry import NodeRegistry


def test_register_get_unregister():
    reg = NodeRegistry()
    reg.register("n1", None)
    assert reg.get("n1") is not None and reg.list_ids() == ["n1"]
    reg.unregister("n1")
    assert reg.get("n1") is None


def test_listeners():
    reg = NodeRegistry()
    down, up = [], []
    reg.on_node_down(down.append)
    reg.on_node_up(up.append)
    reg.register("n1", None)
    reg.unregister("n1")
    reg.register("n1", None)
    assert down == ["n1"] and up == ["n1"]