# Catalogs: the scramblers and CRCs `iq-frames` searches

`tools/iq-frames` looks for a scrambler (a keystream XORed over the bytes) and a CRC
(a check over them) that fit a set of frames. What it can find is what is listed here, so
the lists are kept as data, here and in `tools/fm_receiver/iqcatalog.py`, and
`tools/tests/test_iq_catalog.py` checks the two agree and that every entry reproduces its
published reference. Add to both together; the test fails if they differ.

How to read a hit, and why a long list is not free: a per-frame seed over 255 states makes
an 8-bit CRC match by chance on every frame, so `iq-frames` does not try a hypothesis
whose chance of a hit per frame is above 5 per cent, counts byte-identical frames once,
counts frames that share the checked bytes once, and reports each hit's `p_chance` and
`p_adjusted` (times the hypotheses tried). A hit under 0.001 adjusted is listed. Adding
entries raises the number tried, which weakens every hit a little: add what a
real system uses, not everything that can be written.

## CRCs

Every width 8, 16, 24 and 32 entry of Greg Cook's catalogue of parametrised CRC
algorithms, <https://reveng.sourceforge.io/crc-catalogue/all.htm> (fetched 2026-10-15,
71 entries). Rocksoft model: the register starts at `init`, `refin` reflects each input
byte, `refout` reflects the register at the end, then it is XORed with `xorout`. `check`
is the CRC of the ASCII bytes `123456789`; the test computes it for every entry.

| name | width | poly | init | refin | refout | xorout | check |
|---|---|---|---|---|---|---|---|
| CRC-8/AUTOSAR | 8 | 0x2F | 0xFF | False | False | 0xFF | 0xDF |
| CRC-8/BLUETOOTH | 8 | 0xA7 | 0x0 | True | True | 0x0 | 0x26 |
| CRC-8/CDMA2000 | 8 | 0x9B | 0xFF | False | False | 0x0 | 0xDA |
| CRC-8/DARC | 8 | 0x39 | 0x0 | True | True | 0x0 | 0x15 |
| CRC-8/DVB-S2 | 8 | 0xD5 | 0x0 | False | False | 0x0 | 0xBC |
| CRC-8/GSM-A | 8 | 0x1D | 0x0 | False | False | 0x0 | 0x37 |
| CRC-8/GSM-B | 8 | 0x49 | 0x0 | False | False | 0xFF | 0x94 |
| CRC-8/HITAG | 8 | 0x1D | 0xFF | False | False | 0x0 | 0xB4 |
| CRC-8/I-432-1 | 8 | 0x7 | 0x0 | False | False | 0x55 | 0xA1 |
| CRC-8/I-CODE | 8 | 0x1D | 0xFD | False | False | 0x0 | 0x7E |
| CRC-8/LTE | 8 | 0x9B | 0x0 | False | False | 0x0 | 0xEA |
| CRC-8/MAXIM-DOW | 8 | 0x31 | 0x0 | True | True | 0x0 | 0xA1 |
| CRC-8/MIFARE-MAD | 8 | 0x1D | 0xC7 | False | False | 0x0 | 0x99 |
| CRC-8/NRSC-5 | 8 | 0x31 | 0xFF | False | False | 0x0 | 0xF7 |
| CRC-8/OPENSAFETY | 8 | 0x2F | 0x0 | False | False | 0x0 | 0x3E |
| CRC-8/ROHC | 8 | 0x7 | 0xFF | True | True | 0x0 | 0xD0 |
| CRC-8/SAE-J1850 | 8 | 0x1D | 0xFF | False | False | 0xFF | 0x4B |
| CRC-8/SMBUS | 8 | 0x7 | 0x0 | False | False | 0x0 | 0xF4 |
| CRC-8/TECH-3250 | 8 | 0x1D | 0xFF | True | True | 0x0 | 0x97 |
| CRC-8/WCDMA | 8 | 0x9B | 0x0 | True | True | 0x0 | 0x25 |
| CRC-16/ARC | 16 | 0x8005 | 0x0 | True | True | 0x0 | 0xBB3D |
| CRC-16/CDMA2000 | 16 | 0xC867 | 0xFFFF | False | False | 0x0 | 0x4C06 |
| CRC-16/CMS | 16 | 0x8005 | 0xFFFF | False | False | 0x0 | 0xAEE7 |
| CRC-16/DDS-110 | 16 | 0x8005 | 0x800D | False | False | 0x0 | 0x9ECF |
| CRC-16/DECT-R | 16 | 0x589 | 0x0 | False | False | 0x1 | 0x7E |
| CRC-16/DECT-X | 16 | 0x589 | 0x0 | False | False | 0x0 | 0x7F |
| CRC-16/DNP | 16 | 0x3D65 | 0x0 | True | True | 0xFFFF | 0xEA82 |
| CRC-16/EN-13757 | 16 | 0x3D65 | 0x0 | False | False | 0xFFFF | 0xC2B7 |
| CRC-16/GENIBUS | 16 | 0x1021 | 0xFFFF | False | False | 0xFFFF | 0xD64E |
| CRC-16/GSM | 16 | 0x1021 | 0x0 | False | False | 0xFFFF | 0xCE3C |
| CRC-16/IBM-3740 | 16 | 0x1021 | 0xFFFF | False | False | 0x0 | 0x29B1 |
| CRC-16/IBM-SDLC | 16 | 0x1021 | 0xFFFF | True | True | 0xFFFF | 0x906E |
| CRC-16/ISO-IEC-14443-3-A | 16 | 0x1021 | 0xC6C6 | True | True | 0x0 | 0xBF05 |
| CRC-16/KERMIT | 16 | 0x1021 | 0x0 | True | True | 0x0 | 0x2189 |
| CRC-16/LJ1200 | 16 | 0x6F63 | 0x0 | False | False | 0x0 | 0xBDF4 |
| CRC-16/M17 | 16 | 0x5935 | 0xFFFF | False | False | 0x0 | 0x772B |
| CRC-16/MAXIM-DOW | 16 | 0x8005 | 0x0 | True | True | 0xFFFF | 0x44C2 |
| CRC-16/MCRF4XX | 16 | 0x1021 | 0xFFFF | True | True | 0x0 | 0x6F91 |
| CRC-16/MODBUS | 16 | 0x8005 | 0xFFFF | True | True | 0x0 | 0x4B37 |
| CRC-16/NRSC-5 | 16 | 0x80B | 0xFFFF | True | True | 0x0 | 0xA066 |
| CRC-16/OPENSAFETY-A | 16 | 0x5935 | 0x0 | False | False | 0x0 | 0x5D38 |
| CRC-16/OPENSAFETY-B | 16 | 0x755B | 0x0 | False | False | 0x0 | 0x20FE |
| CRC-16/PROFIBUS | 16 | 0x1DCF | 0xFFFF | False | False | 0xFFFF | 0xA819 |
| CRC-16/RIELLO | 16 | 0x1021 | 0xB2AA | True | True | 0x0 | 0x63D0 |
| CRC-16/SPI-FUJITSU | 16 | 0x1021 | 0x1D0F | False | False | 0x0 | 0xE5CC |
| CRC-16/T10-DIF | 16 | 0x8BB7 | 0x0 | False | False | 0x0 | 0xD0DB |
| CRC-16/TELEDISK | 16 | 0xA097 | 0x0 | False | False | 0x0 | 0xFB3 |
| CRC-16/TMS37157 | 16 | 0x1021 | 0x89EC | True | True | 0x0 | 0x26B1 |
| CRC-16/UMTS | 16 | 0x8005 | 0x0 | False | False | 0x0 | 0xFEE8 |
| CRC-16/USB | 16 | 0x8005 | 0xFFFF | True | True | 0xFFFF | 0xB4C8 |
| CRC-16/XMODEM | 16 | 0x1021 | 0x0 | False | False | 0x0 | 0x31C3 |
| CRC-24/BLE | 24 | 0x65B | 0x555555 | True | True | 0x0 | 0xC25A56 |
| CRC-24/FLEXRAY-A | 24 | 0x5D6DCB | 0xFEDCBA | False | False | 0x0 | 0x7979BD |
| CRC-24/FLEXRAY-B | 24 | 0x5D6DCB | 0xABCDEF | False | False | 0x0 | 0x1F23B8 |
| CRC-24/INTERLAKEN | 24 | 0x328B63 | 0xFFFFFF | False | False | 0xFFFFFF | 0xB4F3E6 |
| CRC-24/LTE-A | 24 | 0x864CFB | 0x0 | False | False | 0x0 | 0xCDE703 |
| CRC-24/LTE-B | 24 | 0x800063 | 0x0 | False | False | 0x0 | 0x23EF52 |
| CRC-24/OPENPGP | 24 | 0x864CFB | 0xB704CE | False | False | 0x0 | 0x21CF02 |
| CRC-24/OS-9 | 24 | 0x800063 | 0xFFFFFF | False | False | 0xFFFFFF | 0x200FA5 |
| CRC-32/AIXM | 32 | 0x814141AB | 0x0 | False | False | 0x0 | 0x3010BF7F |
| CRC-32/AUTOSAR | 32 | 0xF4ACFB13 | 0xFFFFFFFF | True | True | 0xFFFFFFFF | 0x1697D06A |
| CRC-32/BASE91-D | 32 | 0xA833982B | 0xFFFFFFFF | True | True | 0xFFFFFFFF | 0x87315576 |
| CRC-32/BZIP2 | 32 | 0x4C11DB7 | 0xFFFFFFFF | False | False | 0xFFFFFFFF | 0xFC891918 |
| CRC-32/CD-ROM-EDC | 32 | 0x8001801B | 0x0 | True | True | 0x0 | 0x6EC2EDC4 |
| CRC-32/CKSUM | 32 | 0x4C11DB7 | 0x0 | False | False | 0xFFFFFFFF | 0x765E7680 |
| CRC-32/ISCSI | 32 | 0x1EDC6F41 | 0xFFFFFFFF | True | True | 0xFFFFFFFF | 0xE3069283 |
| CRC-32/ISO-HDLC | 32 | 0x4C11DB7 | 0xFFFFFFFF | True | True | 0xFFFFFFFF | 0xCBF43926 |
| CRC-32/JAMCRC | 32 | 0x4C11DB7 | 0xFFFFFFFF | True | True | 0x0 | 0x340BC6D9 |
| CRC-32/MEF | 32 | 0x741B8CD7 | 0xFFFFFFFF | True | True | 0x0 | 0xD2C22F51 |
| CRC-32/MPEG-2 | 32 | 0x4C11DB7 | 0xFFFFFFFF | False | False | 0x0 | 0x376E6E7 |
| CRC-32/XFER | 32 | 0xAF | 0x0 | False | False | 0x0 | 0xBD0BE338 |

### Machine-readable (the test parses this block)

```json
[
 {
  "name": "CRC-8/AUTOSAR",
  "width": 8,
  "poly": "0x2F",
  "init": "0xFF",
  "refin": false,
  "refout": false,
  "xorout": "0xFF",
  "check": "0xDF"
 },
 {
  "name": "CRC-8/BLUETOOTH",
  "width": 8,
  "poly": "0xA7",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x26"
 },
 {
  "name": "CRC-8/CDMA2000",
  "width": 8,
  "poly": "0x9B",
  "init": "0xFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xDA"
 },
 {
  "name": "CRC-8/DARC",
  "width": 8,
  "poly": "0x39",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x15"
 },
 {
  "name": "CRC-8/DVB-S2",
  "width": 8,
  "poly": "0xD5",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xBC"
 },
 {
  "name": "CRC-8/GSM-A",
  "width": 8,
  "poly": "0x1D",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x37"
 },
 {
  "name": "CRC-8/GSM-B",
  "width": 8,
  "poly": "0x49",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0xFF",
  "check": "0x94"
 },
 {
  "name": "CRC-8/HITAG",
  "width": 8,
  "poly": "0x1D",
  "init": "0xFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xB4"
 },
 {
  "name": "CRC-8/I-432-1",
  "width": 8,
  "poly": "0x7",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x55",
  "check": "0xA1"
 },
 {
  "name": "CRC-8/I-CODE",
  "width": 8,
  "poly": "0x1D",
  "init": "0xFD",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x7E"
 },
 {
  "name": "CRC-8/LTE",
  "width": 8,
  "poly": "0x9B",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xEA"
 },
 {
  "name": "CRC-8/MAXIM-DOW",
  "width": 8,
  "poly": "0x31",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0xA1"
 },
 {
  "name": "CRC-8/MIFARE-MAD",
  "width": 8,
  "poly": "0x1D",
  "init": "0xC7",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x99"
 },
 {
  "name": "CRC-8/NRSC-5",
  "width": 8,
  "poly": "0x31",
  "init": "0xFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xF7"
 },
 {
  "name": "CRC-8/OPENSAFETY",
  "width": 8,
  "poly": "0x2F",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x3E"
 },
 {
  "name": "CRC-8/ROHC",
  "width": 8,
  "poly": "0x7",
  "init": "0xFF",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0xD0"
 },
 {
  "name": "CRC-8/SAE-J1850",
  "width": 8,
  "poly": "0x1D",
  "init": "0xFF",
  "refin": false,
  "refout": false,
  "xorout": "0xFF",
  "check": "0x4B"
 },
 {
  "name": "CRC-8/SMBUS",
  "width": 8,
  "poly": "0x7",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xF4"
 },
 {
  "name": "CRC-8/TECH-3250",
  "width": 8,
  "poly": "0x1D",
  "init": "0xFF",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x97"
 },
 {
  "name": "CRC-8/WCDMA",
  "width": 8,
  "poly": "0x9B",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x25"
 },
 {
  "name": "CRC-16/ARC",
  "width": 16,
  "poly": "0x8005",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0xBB3D"
 },
 {
  "name": "CRC-16/CDMA2000",
  "width": 16,
  "poly": "0xC867",
  "init": "0xFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x4C06"
 },
 {
  "name": "CRC-16/CMS",
  "width": 16,
  "poly": "0x8005",
  "init": "0xFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xAEE7"
 },
 {
  "name": "CRC-16/DDS-110",
  "width": 16,
  "poly": "0x8005",
  "init": "0x800D",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x9ECF"
 },
 {
  "name": "CRC-16/DECT-R",
  "width": 16,
  "poly": "0x589",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x1",
  "check": "0x7E"
 },
 {
  "name": "CRC-16/DECT-X",
  "width": 16,
  "poly": "0x589",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x7F"
 },
 {
  "name": "CRC-16/DNP",
  "width": 16,
  "poly": "0x3D65",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFF",
  "check": "0xEA82"
 },
 {
  "name": "CRC-16/EN-13757",
  "width": 16,
  "poly": "0x3D65",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFF",
  "check": "0xC2B7"
 },
 {
  "name": "CRC-16/GENIBUS",
  "width": 16,
  "poly": "0x1021",
  "init": "0xFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFF",
  "check": "0xD64E"
 },
 {
  "name": "CRC-16/GSM",
  "width": 16,
  "poly": "0x1021",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFF",
  "check": "0xCE3C"
 },
 {
  "name": "CRC-16/IBM-3740",
  "width": 16,
  "poly": "0x1021",
  "init": "0xFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x29B1"
 },
 {
  "name": "CRC-16/IBM-SDLC",
  "width": 16,
  "poly": "0x1021",
  "init": "0xFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFF",
  "check": "0x906E"
 },
 {
  "name": "CRC-16/ISO-IEC-14443-3-A",
  "width": 16,
  "poly": "0x1021",
  "init": "0xC6C6",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0xBF05"
 },
 {
  "name": "CRC-16/KERMIT",
  "width": 16,
  "poly": "0x1021",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x2189"
 },
 {
  "name": "CRC-16/LJ1200",
  "width": 16,
  "poly": "0x6F63",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xBDF4"
 },
 {
  "name": "CRC-16/M17",
  "width": 16,
  "poly": "0x5935",
  "init": "0xFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x772B"
 },
 {
  "name": "CRC-16/MAXIM-DOW",
  "width": 16,
  "poly": "0x8005",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFF",
  "check": "0x44C2"
 },
 {
  "name": "CRC-16/MCRF4XX",
  "width": 16,
  "poly": "0x1021",
  "init": "0xFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x6F91"
 },
 {
  "name": "CRC-16/MODBUS",
  "width": 16,
  "poly": "0x8005",
  "init": "0xFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x4B37"
 },
 {
  "name": "CRC-16/NRSC-5",
  "width": 16,
  "poly": "0x80B",
  "init": "0xFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0xA066"
 },
 {
  "name": "CRC-16/OPENSAFETY-A",
  "width": 16,
  "poly": "0x5935",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x5D38"
 },
 {
  "name": "CRC-16/OPENSAFETY-B",
  "width": 16,
  "poly": "0x755B",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x20FE"
 },
 {
  "name": "CRC-16/PROFIBUS",
  "width": 16,
  "poly": "0x1DCF",
  "init": "0xFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFF",
  "check": "0xA819"
 },
 {
  "name": "CRC-16/RIELLO",
  "width": 16,
  "poly": "0x1021",
  "init": "0xB2AA",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x63D0"
 },
 {
  "name": "CRC-16/SPI-FUJITSU",
  "width": 16,
  "poly": "0x1021",
  "init": "0x1D0F",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xE5CC"
 },
 {
  "name": "CRC-16/T10-DIF",
  "width": 16,
  "poly": "0x8BB7",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xD0DB"
 },
 {
  "name": "CRC-16/TELEDISK",
  "width": 16,
  "poly": "0xA097",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xFB3"
 },
 {
  "name": "CRC-16/TMS37157",
  "width": 16,
  "poly": "0x1021",
  "init": "0x89EC",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x26B1"
 },
 {
  "name": "CRC-16/UMTS",
  "width": 16,
  "poly": "0x8005",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xFEE8"
 },
 {
  "name": "CRC-16/USB",
  "width": 16,
  "poly": "0x8005",
  "init": "0xFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFF",
  "check": "0xB4C8"
 },
 {
  "name": "CRC-16/XMODEM",
  "width": 16,
  "poly": "0x1021",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x31C3"
 },
 {
  "name": "CRC-24/BLE",
  "width": 24,
  "poly": "0x65B",
  "init": "0x555555",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0xC25A56"
 },
 {
  "name": "CRC-24/FLEXRAY-A",
  "width": 24,
  "poly": "0x5D6DCB",
  "init": "0xFEDCBA",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x7979BD"
 },
 {
  "name": "CRC-24/FLEXRAY-B",
  "width": 24,
  "poly": "0x5D6DCB",
  "init": "0xABCDEF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x1F23B8"
 },
 {
  "name": "CRC-24/INTERLAKEN",
  "width": 24,
  "poly": "0x328B63",
  "init": "0xFFFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFFFF",
  "check": "0xB4F3E6"
 },
 {
  "name": "CRC-24/LTE-A",
  "width": 24,
  "poly": "0x864CFB",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xCDE703"
 },
 {
  "name": "CRC-24/LTE-B",
  "width": 24,
  "poly": "0x800063",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x23EF52"
 },
 {
  "name": "CRC-24/OPENPGP",
  "width": 24,
  "poly": "0x864CFB",
  "init": "0xB704CE",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x21CF02"
 },
 {
  "name": "CRC-24/OS-9",
  "width": 24,
  "poly": "0x800063",
  "init": "0xFFFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFFFF",
  "check": "0x200FA5"
 },
 {
  "name": "CRC-32/AIXM",
  "width": 32,
  "poly": "0x814141AB",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x3010BF7F"
 },
 {
  "name": "CRC-32/AUTOSAR",
  "width": 32,
  "poly": "0xF4ACFB13",
  "init": "0xFFFFFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFFFFFF",
  "check": "0x1697D06A"
 },
 {
  "name": "CRC-32/BASE91-D",
  "width": 32,
  "poly": "0xA833982B",
  "init": "0xFFFFFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFFFFFF",
  "check": "0x87315576"
 },
 {
  "name": "CRC-32/BZIP2",
  "width": 32,
  "poly": "0x4C11DB7",
  "init": "0xFFFFFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFFFFFF",
  "check": "0xFC891918"
 },
 {
  "name": "CRC-32/CD-ROM-EDC",
  "width": 32,
  "poly": "0x8001801B",
  "init": "0x0",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x6EC2EDC4"
 },
 {
  "name": "CRC-32/CKSUM",
  "width": 32,
  "poly": "0x4C11DB7",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0xFFFFFFFF",
  "check": "0x765E7680"
 },
 {
  "name": "CRC-32/ISCSI",
  "width": 32,
  "poly": "0x1EDC6F41",
  "init": "0xFFFFFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFFFFFF",
  "check": "0xE3069283"
 },
 {
  "name": "CRC-32/ISO-HDLC",
  "width": 32,
  "poly": "0x4C11DB7",
  "init": "0xFFFFFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0xFFFFFFFF",
  "check": "0xCBF43926"
 },
 {
  "name": "CRC-32/JAMCRC",
  "width": 32,
  "poly": "0x4C11DB7",
  "init": "0xFFFFFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0x340BC6D9"
 },
 {
  "name": "CRC-32/MEF",
  "width": 32,
  "poly": "0x741B8CD7",
  "init": "0xFFFFFFFF",
  "refin": true,
  "refout": true,
  "xorout": "0x0",
  "check": "0xD2C22F51"
 },
 {
  "name": "CRC-32/MPEG-2",
  "width": 32,
  "poly": "0x4C11DB7",
  "init": "0xFFFFFFFF",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0x376E6E7"
 },
 {
  "name": "CRC-32/XFER",
  "width": 32,
  "poly": "0xAF",
  "init": "0x0",
  "refin": false,
  "refout": false,
  "xorout": "0x0",
  "check": "0xBD0BE338"
 }
]
```

## Scramblers

Additive LFSRs: a keystream is XORed over the bytes. Two shift conventions, as
`iqcatalog.py` states them:

- `left`: the register shifts left, the output bit is its top bit, the new bit is the
  parity of `reg & mask` and enters at bit 0; the bits pack MSB first.
- `right`: the register shifts right, the output bit is bit 0, the new bit is the parity
  of `reg & mask` and enters at the top bit; the bits pack LSB first.

| name | width | mask | shift | seeds | first_bytes | note |
|---|---|---|---|---|---|---|
| pn9 | 9 | 0x21 | right | 0x1FF | ffe11d9aed853324 | x^9+x^5+1, seed all ones, LSB first |
| ss-lfsr8 | 8 | 0x8E | left | all non-zero (255) | 0163d0ff214faae0 | x^8+x^4+x^3+x^2+1, MSB first, seed per frame |

Where the entries come from, and what checks each:

- **`pn9`**: x^9+x^5+1, all-ones seed, the 8 least significant bits of the register
  taken per byte. It is the data whitener of the CC1101 family and of IEEE 802.15.4g
  (the seed "shall be all ones"). Texas Instruments' design note DN509 (SWRA322,
  <https://www.ti.com/lit/an/swra322/swra322.pdf>) prints the sequence as
  `1111 1111 / 1110 0001 / 0001 1101 / 1001 1010`, which is `FF E1 1D 9A`; the test
  checks those four bytes and the next twelve it generates.
- **`ss-lfsr8`**: x^8+x^4+x^3+x^2+1 (mask `0x8E`), MSB first, the seed chosen per hop.
  It is the payload scrambler of the Silver Spring Networks mesh, as rtl_433's
  `src/devices/silver_spring_mesh.c` (decoder 383) has it. It was checked on air: a
  CRC-32/MPEG-2 over the descrambled payload matched on 32 of 38 captured bursts (see
  [silver-spring.md](silver-spring.md)). The first-bytes value above is this function's
  output for seed 1; it is a regression value, not an independent source.

Seeds: a fixed seed is one value. A per-frame seed is every non-zero state, because a
transmitter's seed is often derived from something the receiver does not see (a channel
number, a counter). With a free seed, where the keystream starts inside the frame cannot be
told from where the CRC starts, since a stream that starts later is another seed's;
`iq-frames` reports the two as equal.

### Machine-readable (the test parses this block)

```json
[
 {
  "name": "pn9",
  "width": 9,
  "mask": "0x21",
  "shift": "right",
  "seeds": "0x1FF",
  "first_bytes": "ffe11d9aed853324",
  "note": "x^9+x^5+1, seed all ones, LSB first"
 },
 {
  "name": "ss-lfsr8",
  "width": 8,
  "mask": "0x8E",
  "shift": "left",
  "seeds": "all non-zero (255)",
  "first_bytes": "0163d0ff214faae0",
  "note": "x^8+x^4+x^3+x^2+1, MSB first, seed per frame"
 }
]
```

## Not in the catalogue, and what to do

- **Other LFSR polynomials or widths.** `iq-frames` lists windows of low linear complexity
  (Berlekamp-Massey) as `lfsr_hints`; a window of 96 bits whose complexity is under a
  quarter of its length obeys a recurrence of about that many stages, and the recurrence
  is the polynomial. Add an entry here with its reference and a test vector.
- **BLE data whitening, Wi-SUN, Z-Wave, Wireless M-Bus and others.** Not added: no
  reference vector has been checked on this machine. Add one with the vector in the same
  change.
- **Encrypted bodies.** A cipher keyed per device does not appear as a short recurrence
  or a catalogue CRC; the search says nothing, which is the answer.
