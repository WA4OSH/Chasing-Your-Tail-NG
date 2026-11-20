# CYT-NG configuration contract

Phase 2A provides the configuration API for future legacy entry-point integration. Use
`sample.config.json` as the starting point for a new schema v2 configuration. Keep the
operator's `config.json` local. Legacy commands keep their v1 file layout and explicitly reject v2. Keep that file in place while testing v2 with
an explicit filename. If your checkout predates the removal of tracked `config.json`,
back up your operator copy outside the checkout before switching to or pulling a
revision that removes it from Git, then restore it locally. The `cyt_core` commands continue to use their CLI options directly.

## Schema v2

Every field below is required except `search`. The sample shows suggested starting values.
The v2 validator requires those values to be explicit. All objects reject unknown fields.

| Field | Type and meaning |
| --- | --- |
| `schema_version` | Integer `2`. |
| `paths.log_dir` | Nonblank path string for legacy CYT logs. |
| `paths.kismet_logs` | Nonblank path or glob string for Kismet capture inputs. |
| `paths.ignore_lists.mac` | Nonblank path string for the MAC ignore list. |
| `paths.ignore_lists.ssid` | Nonblank path string for the SSID ignore list. |
| `timing.poll_interval_seconds` | Positive finite number, in seconds. |
| `timing.tracking_list_rotation_cycles` | Positive integer, in polls per rotation. |
| `timing.time_windows` | Four required positive, strictly increasing offsets in minutes before now. In order, `recent_minutes`, `medium_minutes`, `old_minutes`, and `oldest_minutes`. |
| `search` | Optional WiGLE enrichment bounding box. If present, supply all four finite decimal degree fields, `lat_min`, `lat_max`, `lon_min`, and `lon_max`. Latitude is in −90 to 90 and longitude in −180 to 180, inclusive. Each minimum must be less than its maximum. Antimeridian-crossing boxes and polygons are unsupported. Phase 2A does not apply this setting to runtime behavior. |

Credentials belong outside the configuration file. The API rejects credential fields,
including the legacy `api_keys` tree, in both versions through an explicit field allowlist.
The legacy `api_keys` field can be read only through the explicit credential migration
command. Startup does not migrate credentials. Keep secrets in the credential
provider and out of paths and other ordinary string values. JSON must be UTF-8 and contain
a single object. Duplicate object keys, non-finite numbers, unsupported versions, and
incorrect types are rejected. Booleans are distinct from numbers. Diagnostics identify
the filename and field. Syntax errors include line and column, and field errors from
`load_config` point to the key or enclosing object when the key is missing.

## Paths and legacy compatibility

Absolute paths keep their existing base. In v2, relative paths are based on the directory
of the filename passed to the loader, including both ignore-list paths. The loader uses the
path used to open the config, including when it names a symlink. It does not expand home
or environment variables, glob, check for existing capture files, or create directories.
Relative config filenames use the explicit working directory supplied to the loader, or
the launch working directory by default. `settings.path_provenance` provides the
config source and exact decoded path strings as written in that file. The matching
fields in `settings.paths` provide the resolved runtime paths. For example, a missing
capture diagnostic can show the source file, original glob, and resolved glob.

An omitted `schema_version` or integer `1` selects v1. Relative capture and log paths use
the launch working directory. Relative ignore filenames use its `ignore_lists/` directory.
The legacy `paths.base_dir` field is accepted as a path string and has no effect. The v1
timing keys `check_interval` and `list_update_interval` default when absent to 60 seconds
and five cycles. An omitted `time_windows` object defaults to 5, 10, 15, and 20 minutes.
If the object is present, all four v1 keys `recent`, `medium`, `old`, and `oldest` are
required. A value that is present and invalid fails validation in either version.

Schema conversion is explicit. To prepare a v2 file, copy the sample to a new filename,
transfer the desired settings, and adjust relative paths for the v2 anchor. Validate the
new file while retaining the original. Loading a config never rewrites or migrates it.

## Python API

```python
from cyt_config import load_config

settings = load_config("sample.config.json")
# Pass settings.paths, settings.timing, or settings.search to the relevant subsystem.
```

`load_config` is the file-reading boundary. `parse_config` parses text,
`validate_config` constructs frozen typed settings, and `normalize_config` resolves
their paths using explicit inputs. These three steps do not access the filesystem.
Subsystem initialization belongs to the caller.


## Architectural invariants

- **ARCH-002 — Instrument connections preserve instrument state.** Connecting to an
  instrument must not implicitly initialize, reset, or reconfigure it. Those changes
  require an explicit operation authorized by the caller.
- **ARCH-003 — Construction, configuration loading, and inspection preserve operational state.**
  These actions must not implicitly perform destructive initialization or operational
  transitions. Keep configuration validation separate from operational readiness checks.
  A valid configuration can name a capture path that does not yet exist. Check availability,
  permissions, or device state explicitly in the operational layer.
