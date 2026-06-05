# Media Manager Behavior Notes

This note closes Phase 0 for the owned-fork iteration plan. It records the
behavior OpenAver wants to borrow from mature local media managers without
copying their implementation.

## Reference Tools

- Javinizer and MDCx: useful references for naming templates, source priority,
  NFO generation, poster/fanart conventions, and batch organization flow.
- JavLuv and Jvedio: useful references for a local-first media library,
  duplicate handling, manual correction, and per-work metadata editing.
- tinyMediaManager: useful reference for the work/file split, stable sidecar
  naming, NFO compatibility, and bulk repair operations.
- Jellyfin and Kodi: useful references for poster, fanart, extrafanart, and NFO
  layout conventions consumed by media servers.
- pornboss and lightweight organizers: useful references for low-friction
  filename recognition and fast manual review loops.

## OpenAver Contracts Derived From The Audit

- Filename identity is the single source of truth. Case, separator, and compact
  forms such as `SONE-103`, `sone_103`, and `sone103` resolve to one canonical
  work key.
- File variants do not change the work key. `-C`, `-U`, `-UC`, `-1`, and `-A`
  are parsed as structured variant metadata and do not fan out as separate
  external searches.
- FC2 aliases are a source-query concern, not separate works. `FC2PPV-1234567`,
  `FC2-1234567`, and `FC2-PPV-1234567` share one work key while each source can
  receive its preferred query form.
- Local directory labels describe where the file lives. `censored` and
  `uncensored` come from configured scanner directories, not from filename
  suffixes and not from scraper source routing.
- Sidecar paths are resolved centrally. NFO, cover, poster, fanart, and
  extrafanart writers use one resolver so Settings, Scanner, Scraper, and
  Showcase agree on the same disk layout.
- Showcase is work-first. Cards represent works; the detail payload lists the
  concrete files and their variant flags.

## Phase 0 Filename Matrix Source

The executable filename matrix lives in:

```text
tests/fixtures/filename_variants/matrix.json
```

Tests in `tests/unit/test_filename_identity.py` load that matrix so future
parser changes cannot drift away from the owned-fork plan.
