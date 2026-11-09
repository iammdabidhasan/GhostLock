# Contributing

Thanks for helping! Useful areas: accessibility, Windows edge cases (multi-monitor, mixed DPI, RDP), UI polish, security review, translations.

1. Fork, create a branch, keep the change focused.
2. Run the tests: `py -m unittest discover -s tests`
3. If you touch the UI, include a screenshot. If you touch icons, edit `tools/make_icons.py` and regenerate.
4. Build locally (`.\build.ps1 -SetupOnly`) when changing packaging.
5. Update `README.md` and `CHANGELOG.md` when behaviour changes.

Please keep creator/publisher attribution (MD ABID HASAN / MDexpLab) in distributions. Modified builds must not be presented as official releases.
