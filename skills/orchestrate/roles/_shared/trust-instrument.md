## Trust an instrument only once it has been checked

A tool that fails quietly gives a *plausible* number, not an error — `TOTAL 0` with exit 0, a
reader with the wrong bit order, a grep that counts summary lines. So:

- Before you quote a measurement or a "none found", run the instrument once on an input whose
  answer you already know, and name the instrument when you report the number.
- No output is not "none exist": rule out wrong path, pattern or scope first.
- Before you say a file is generated, find what writes it — reading, linking or uploading it
  doesn't count.
- Before you say a threshold is nearly breached, or propose loosening a check, run the check itself.

