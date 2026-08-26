# Contributing

Thanks for helping improve the annotation workflow.

## Before opening a pull request

1. Run `python -m unittest discover -s tests -v`.
2. Run `python -m compileall -q annotation_platform scripts tests`.
3. Keep the server dependency-light; explain any new runtime dependency.
4. Add a regression test when fixing data loss, attribution, concurrency, or counting logic.

## Data rules

Only commit synthetic fixtures. Never submit real student images, annotation databases, exported datasets, audit logs, names, invite codes, cookies, credentials, internal hostnames, or production screenshots.

If a bug can only be reproduced with real data, reduce it to the smallest synthetic fixture before opening an issue or pull request.
