"""Русские словоформы для чисел: 1 находка, 2 находки, 5 находок."""


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    n1 = n % 10
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def nform(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {plural(n, one, few, many)}"


def findings_n(n: int) -> str:
    return nform(n, "находка", "находки", "находок")
