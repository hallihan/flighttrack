import os

# Tests never open a real window. Must be set before pygame is imported anywhere.
os.environ["SDL_VIDEODRIVER"] = "dummy"

import pytest  # noqa: E402

from flighttrack import net, portal  # noqa: E402


@pytest.fixture(autouse=True)
def _no_waiting_between_fake_requests():
    """The rate limiter is shared by the whole process; tests talk to fakes, and must not
    wait on each other's turns. test_net.py tests the limiter itself."""
    net.LIMITER.enabled = False
    yield
    net.LIMITER.enabled = True


@pytest.fixture(autouse=True)
def _web_page_open_for_route_tests():
    """Tests of the web page's routes would otherwise all start by signing in. The tests of
    signing in itself turn the password back on (test_signin.py)."""
    portal.PASSWORD_REQUIRED = False
    yield
    portal.PASSWORD_REQUIRED = True
