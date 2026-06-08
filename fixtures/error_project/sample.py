def add(a: int, b: int) -> int:
    return a + b


wrong: int = add("not", "ints")  # two type errors: str args to int params
bad: str = 123  # assignment type error
