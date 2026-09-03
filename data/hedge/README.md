# HEDGE hardware-experiment data

Fetched, not vendored. `make data` (or `python data/hedge/fetch_hedge.py`)
pulls fifteen files from
[hedge-wan/hedge](https://github.com/hedge-wan/hedge) at commit
`9c6540cf042a4933e918f9b306fcf116d8776e2f` and refuses any whose SHA-256
does not match the pins in `src/ocintent/hedge.py`.

The upstream repository carries no license file. The data is used here the
way the paper asks — cited, read in place, never redistributed. Cite:

> Devraj et al., *HEDGE*, 23rd USENIX Symposium on Networked Systems Design
> and Implementation (NSDI '26).

| run | disturbance | figure | files |
| --- | --- | --- | --- |
| `wdl` | 3/4-inch macrobend, three C-band wavelengths at 16-QAM | 4a, 4b | `transponder_data.csv`, `server1-4.log` |
| `mod_formats` | the same bend, PM-QPSK / 8-QAM / 16-QAM | 4c | same |
| `prototype` | variable optical attenuator, four wavelengths | 14b, 14c | same |

A missing or altered file does not skip anything: `ocintent.hedge` raises,
and every registry point that reads the data turns red.
