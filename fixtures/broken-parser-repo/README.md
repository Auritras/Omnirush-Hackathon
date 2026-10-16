# ProofFix broken parser demo

This is a deliberately small, dependency-free repository for the offline
ProofFix demo. Run the baseline from this directory:

```sh
python -m unittest discover -s tests -v
```

The baseline fails because `parser.py` uses `split("=")` without limiting the
number of splits. A value such as `https://example.test/?mode=fast` therefore
looks like a parser failure even though the key and the first value are valid.
The nearby, misleading symptom is the generic `ParseError` raised by the
parser; the root cause is the incorrect delimiter handling on line 9.

The focused regression case for values containing `=` is intentionally part of
the demo command: it fails before the patch and passes after changing the call
to `split("=", 1)`. The original simple-assignment case passes in the broken
state, demonstrating why the missing edge-case coverage can be misleading.

Reset the fixture to the known broken state at any time, without network access
or third-party packages:

```sh
sh reset.sh
```
