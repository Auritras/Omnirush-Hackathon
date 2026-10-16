"""A tiny intentionally broken key/value parser for the ProofFix demo."""


class ParseError(ValueError):
    """Raised when an input line is not a key/value assignment."""


def parse_assignment(line: str):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "=" not in line:
        raise ParseError("expected key=value")
    # BUG: values containing an equals sign are split into too many pieces.
    key, value = line.split("=")
    if not key.strip():
        raise ParseError("missing key")
    return key.strip(), value.strip()


def parse(document: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in document.splitlines():
        assignment = parse_assignment(line)
        if assignment is not None:
            key, value = assignment
            result[key] = value
    return result


if __name__ == "__main__":
    print(parse("mode=fast"))
