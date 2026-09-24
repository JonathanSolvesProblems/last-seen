import socket

import pytest


@pytest.fixture
def free_port() -> int:
    """An unused TCP port.

    Tests used to hardcode ports, so a stray simulator or a bench running in
    another terminal made them fail with OSError and look like a real defect.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
