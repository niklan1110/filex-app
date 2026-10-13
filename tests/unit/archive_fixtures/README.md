# RAR fixtures for `test_archive_checks.py`

RAR archives cannot be written with free tools, so these come from libarchive's own test suite
(tag v3.7.9, `libarchive/test/*.rar.uu`, uudecoded). Licence: BSD-2-Clause (libarchive).

| File | Upstream file | Contents |
|---|---|---|
| `rar4_symlink.rar` | `test_read_format_rar.rar.uu` | RAR4: two files, a symlink `testlink`, two dirs |
| `rar5_symlink.rar` | `test_read_format_rar5_symlink.rar.uu` | RAR5: a file, a file symlink, a dir symlink |
| `rar5_hardlink.rar` | `test_read_format_rar5_hardlink.rar.uu` | RAR5: a file and a hard link to it |
| `rar5_solid.rar` | `test_read_format_rar5_solid.rar.uu` | RAR5 solid: seven regular files |
| `rar5_stored.rar` | `test_read_format_rar5_stored.rar.uu` | RAR5, stored: one text file |
