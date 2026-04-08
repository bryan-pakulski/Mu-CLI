from calculator import add, subtract, multiply, divide


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(10, 4) == 6


def test_multiply():
    assert multiply(6, 7) == 42


def test_divide_by_zero():
    try:
        divide(1, 0)
        assert False, "expected ValueError"
    except ValueError:
        assert True
