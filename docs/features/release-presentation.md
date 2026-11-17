# Release presentation

## Completed cleanup

- The About page shows product version, runtime and open-source licenses without exposing backend names or implementation paths.
- The Reagents page no longer shows a disabled government traceability placeholder. Batch lookup, validation history and normal error handling remain available.
- The former instrument settings page is titled “设置”; its algorithm backend card is removed. Startup configuration and data folder controls use user-facing names and explain when changes take effect.
- Tube detail uses “检测结果” for analysis output and keeps the review conclusion distinct.
- Analysis contract and worker timeout alerts use actionable user-facing wording instead of interface-contract and thread implementation details.
- Authentication and experiment helper copy was updated by the release integration work; its new source strings are included in the translation catalogs.
- Reports and exports were reviewed for replacement-implementation labels; none were found in user-facing report or export text.
- Chinese, Traditional Chinese and English catalogs were refreshed for the UI copy changes and checked against the extracted sources.

Internal implementation comments and technical logs remain available to maintainers. Application startup and login helper copy was updated by the release integration work.
