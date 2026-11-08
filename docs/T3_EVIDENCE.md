# Tranche 3 — Deliverable evidence (mainnet)

Anchor: [`anchor.versotek.io`](https://anchor.versotek.io) · Network: **Stellar mainnet** (`Public Global Stellar
Network ; September 2015`) · Live since **2026-10-16** · Hot wallet (Turnkey custody):
[`GCYWYXFOWGOXOLUI6ZXBSMMRRK4KJUUVHBBSPMM6XAEOAX2YU5KZNJMM`](https://stellar.expert/explorer/public/account/GCYWYXFOWGOXOLUI6ZXBSMMRRK4KJUUVHBBSPMM6XAEOAX2YU5KZNJMM)
· Asset: USDC (Circle) `GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN`

Back to the [README](../README.md) · Tranche 2 evidence (testnet): [`T2_EVIDENCE.md`](T2_EVIDENCE.md)

**Sources.** On-chain data comes from the public Horizon API (`horizon.stellar.org`, payments of the hot wallet,
queried on 2026-11-08) and can be checked by anyone on Stellar Expert. Statuses and settlement times come from the
mainnet admin (Polaris `Transaction` and VERSO's SEP-24 metadata), cross-checked with the on-chain payment times.
Endpoint checks were run against the live anchor on 2026-11-08; the SDF anchor test suite was run on 2026-10-16.

## Summary

| #   | Deliverable                                  | Amount  | Result                                                                                                                       |
| --- | -------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 1   | Mainnet launch SEP-1/10/24/38                | $15,000 | **Covered** — all four SEPs live on mainnet since 2026-10-16; hot wallet with USDC trustline; SEP-1 5/5, SEP-10 14/17         |
| 2   | CCI/CCE Phase 2 semi-automated settlement    | $12,000 | **Covered** — 30 consecutive real operations completed, average settlement **~5.4 min**, 3 Peruvian banks, runbook published |
| 3   | Operational proof + Stellar Anchor Directory | $3,000  | **Covered, with a deviation** — 30 operations on Stellar Expert; Directory replaced by open access from any Stellar wallet   |
| 4   | Security hardening + audit preparation       | $8,000  | **Covered** — callbacks reviewed, Turnkey signing verified, edge-case suite in CI (51 tests, 340 in total), 6 issues fixed   |
| 5   | Public technical documentation               | $6,000  | **Covered** — implementation guide, mainnet architecture, callback reference and operator runbook on GitHub                  |

Deviations from the proposal, explained in each section: **Turnkey instead of AWS KMS** (D1, D4), **separate
treasury wallet instead of a cold-wallet dual-approval protocol** (D1), **open wallet access instead of an Anchor
Directory submission** (D3), and **fiat receipt verified manually in the bank instead of an automatic bank alert**
(D2, not a measurement criterion).

---

## Deliverable 1 — Mainnet launch: SEP-1/10/24/38

| Criterion                                                      | Evidence                                                                                                                                                                                                                                                                                                                                             |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hot wallet active on mainnet with USDC trustline               | [`GCYW…NJMM`](https://stellar.expert/explorer/public/account/GCYWYXFOWGOXOLUI6ZXBSMMRRK4KJUUVHBBSPMM6XAEOAX2YU5KZNJMM): trustline to Circle USDC created on 2026-10-14 and **signed by Turnkey** ([`8d7e05e2…`](https://stellar.expert/explorer/public/tx/8d7e05e29ae29be7f987b7870e9fc909815a8cc0a8fe869e53b69681c9af1643)); 30 client operations and every payout since then |
| `stellar.toml` for mainnet with zero validation errors         | [`/.well-known/stellar.toml`](https://anchor.versotek.io/.well-known/stellar.toml) passes **SEP-1 5/5** in the SDF anchor test suite (`@stellar/anchor-tests`). Stellar Laboratory no longer validates `stellar.toml`, so the official CLI suite is used instead                                                                                     |
| SEP-10 authentication operational on mainnet                   | `GET /auth` returns a mainnet challenge (below); **SEP-10 14/17** in the SDF suite. The 3 remaining tests (_Account Signer Support_) create and fund test accounts with extra signers, which the suite can only do on testnet; they pass against the testnet deployment. Every one of the 30 operations started with a SEP-10 login on mainnet       |
| SEP-38 live quotes displayed in a wallet before each real transaction | Every operation shows the live rate, the amount sent and the amount received in the SEP-24 webview before the client confirms. The same price is served by `/sep38/price` and `/sep38/quote`. Example: the first operation (on-ramp USD 2.00 → 1.9782394 USDC, 2026-10-18) used SEP-38 quote `a4972682…` at a price of 1.011, shown before confirmation |

### Mainnet configuration in the codebase

- **USDC issuer** `GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN` is defined in
  [`verso_integrations/sep1.py`](../backend/verso_integrations/sep1.py) (`USDC_ISSUER_MAINNET`). The issuer, the
  `stellar.toml` `status` (`live` / `test`) and the assets created by `seed_polaris_t2` are selected from the network
  passphrase, so the same code serves both networks.
- **"Feature flag" to mainnet.** The switch is a separate deployment environment of the same repository: mainnet sets
  `STELLAR_NETWORK_PASSPHRASE` to the public network and `HORIZON_URI` to `https://horizon.stellar.org` (Polaris reads
  both independently), its own Stellar RPC, database, hot wallet and SEP-10 key. The release was validated on testnet
  first; mainnet auto-deploy is off and every release is deployed manually after testnet.
- **SEP endpoints** published in the mainnet `stellar.toml` (checked 2026-11-08):

```toml
NETWORK_PASSPHRASE = "Public Global Stellar Network ; September 2015"
ACCOUNTS = [ "GCYWYXFOWGOXOLUI6ZXBSMMRRK4KJUUVHBBSPMM6XAEOAX2YU5KZNJMM" ]
SIGNING_KEY = "<SEP-10 key: signs login challenges only, holds no funds>"
TRANSFER_SERVER_SEP0024 = "https://anchor.versotek.io/sep24"
WEB_AUTH_ENDPOINT = "https://anchor.versotek.io/auth"
ANCHOR_QUOTE_SERVER = "https://anchor.versotek.io/sep38"

[[CURRENCIES]]
code = "USDC"
issuer = "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN"
status = "live"
anchor_asset_type = "fiat"
anchor_asset = "PEN"      # a second entry has anchor_asset = "USD"
```

### Live endpoint checks (2026-11-08)

| Endpoint                                   | Result                                                                                                                       |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `GET /auth?account=G…`                     | SEP-10 challenge for the public network: sequence 0, `manage_data` `anchor.versotek.io auth` + `web_auth_domain`, 1 signature (the anchor's) |
| `GET /sep24/info`                          | `deposit.USDC.enabled = true`, `withdraw.USDC.enabled = true`, account creation and claimable balances supported            |
| `GET /sep38/info`                          | `stellar:USDC:GA5Z…KZVN`, `iso4217:PEN`, `iso4217:USD`, delivery method `bank_transfer_cci_cce` (sell and buy)              |
| Hot wallet on Horizon                      | One signer (the Turnkey key), USDC trustline to `GA5Z…KZVN` active                                                           |

### Deviations

- **AWS KMS → Turnkey.** AWS KMS cannot sign Ed25519, the signature scheme of Stellar. The hot wallet key was
  generated inside Turnkey (hardware-isolated enclaves) and never exists on VERSO servers; payouts are signed through
  Polaris' official `CustodyIntegration` ([`custody.py`](../backend/verso_integrations/custody.py)). Details: README →
  _Architecture note_, point 2.
- **Cold wallet dual approval → separate treasury wallet.** It appears in the deliverable description, not in its
  measurement criteria. The hot wallet only holds the operating float; the bulk of funds is kept in a separate VERSO
  treasury wallet that is not connected to the anchor or its servers and funds the hot wallet when needed (the
  top-ups are visible in the hot wallet history; see Deliverable 3).

---

## Deliverable 2 — CCI/CCE Phase 2: semi-automated fiat settlement

| Criterion                                                                   | Result                                                                                                       |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Average settlement ≈ 30 min or less across 30 consecutive completed transactions | **~5.4 minutes** on average over **30 consecutive** operations, all `completed`                          |
| Transfers from at least 3 different Peruvian banks                          | **BCP, Alfin Banco and Interbank**                                                                           |
| Operational runbook in the public GitHub repository                         | [`docs/RUNBOOK.md`](RUNBOOK.md)                                                                              |

### The 30 operations

All 30 operations made on mainnet between the first one (2026-10-18) and 2026-10-27: **27 on-ramp** (PEN/USD → USDC)
and **3 off-ramp** (USDC → PEN/USD), from **9 client accounts**. No operation in that period is left out. On-ramp
rows are the anchor's USDC payout; off-ramp rows are the client's USDC payment to the hot wallet, matched by its
hash memo.

| # | Type | Date (UTC) | USDC | Client | Ledger | Stellar tx |
| - | ---- | ---------- | ---: | ------ | -----: | ---------- |
| 1 | On-ramp | 2026-10-18 21:34:23 | 1.9782394 | A | 65014696 | [`1c49fbfa…`](https://stellar.expert/explorer/public/tx/1c49fbfacf18501b113279fa8d5af1774b8839d45b1f42d540e5b0cc5abb496c) |
| 2 | On-ramp | 2026-10-19 20:10:38 | 2.8985507 | B | 65030971 | [`293309d4…`](https://stellar.expert/explorer/public/tx/293309d4cbc4a4cc144791a40b3c9575d40ea4bcaf6b6e3aad0fb45387935e7c) |
| 3 | On-ramp | 2026-10-19 20:28:18 | 26.0869565 | B | 65031183 | [`3734239d…`](https://stellar.expert/explorer/public/tx/3734239dd44f29a67914bcd8ae95da7421354409d7dd95d92840324edcf8acdc) |
| 4 | On-ramp | 2026-10-20 00:22:48 | 5.7971014 | C | 65033997 | [`42d5b038…`](https://stellar.expert/explorer/public/tx/42d5b038d4458e73f79752a2f3c56261db16ce58a2abf866a0463b5d0307d6a4) |
| 5 | On-ramp | 2026-10-20 00:38:28 | 23.1884058 | C | 65034185 | [`19b280fb…`](https://stellar.expert/explorer/public/tx/19b280fb37ad369ab7c920583b64fa73d12a72d7297b6499d63d65c0c311e065) |
| 6 | On-ramp | 2026-10-20 00:49:38 | 14.4927536 | C | 65034319 | [`c13ca6ab…`](https://stellar.expert/explorer/public/tx/c13ca6abea57803ce7b8c5406c560bfce0daf22e82b777be5148d6edbc164739) |
| 7 | On-ramp | 2026-10-20 04:13:18 | 4.3478261 | D | 65036763 | [`3dfde45e…`](https://stellar.expert/explorer/public/tx/3dfde45ec8a522b2d42f67d6ee95cd64c30d929a5df6cb632636056b846d1f32) |
| 8 | On-ramp | 2026-10-21 18:03:03 | 14.4927536 | B | 65064000 | [`0e310f58…`](https://stellar.expert/explorer/public/tx/0e310f58c0de32c42b06b34c150b44aaf35d73cfdb4e3466aa0ecc0b086d548a) |
| 9 | On-ramp | 2026-10-21 18:28:23 | 2.8985507 | E | 65064304 | [`be6d4207…`](https://stellar.expert/explorer/public/tx/be6d42075d3fb1367f0ce319a585222828692205db03c1b2c2ceb40e7679274b) |
| 10 | On-ramp | 2026-10-21 20:48:13 | 4.3478261 | E | 65065982 | [`1da481ba…`](https://stellar.expert/explorer/public/tx/1da481ba6a48d1ba797dd1c3f3f7f1529c3d26aed594cca9ecb3bfbe3b641a85) |
| 11 | On-ramp | 2026-10-21 22:42:18 | 3.1884058 | F | 65067351 | [`99de14e5…`](https://stellar.expert/explorer/public/tx/99de14e5abb890ed44d36f0f7814116ca64568d9282362716c6f24dda06688e6) |
| 12 | On-ramp | 2026-10-21 23:10:58 | 2.6086957 | F | 65067695 | [`309d832a…`](https://stellar.expert/explorer/public/tx/309d832afd2c4c6565a2fe8f0a7dc9907cffa7847d47269de3cc182e64b25946) |
| 13 | On-ramp | 2026-10-22 16:15:38 | 28.8184438 | D | 65079991 | [`29fbc4c2…`](https://stellar.expert/explorer/public/tx/29fbc4c26c8d8cb66f06a15815a2e15645cf7e5bff3ad88519ad02ccf8ea1dfa) |
| 14 | On-ramp | 2026-10-22 16:33:03 | 29.6735905 | D | 65080200 | [`5652a0bd…`](https://stellar.expert/explorer/public/tx/5652a0bdbadf7251e5c932991d7e7d8c05941125d14fa201865bcea5566b1bff) |
| 15 | On-ramp | 2026-10-23 00:55:33 | 2.8818444 | G | 65086230 | [`25cc4139…`](https://stellar.expert/explorer/public/tx/25cc4139a91def7a8ac423ae59662224889d6b5017c5ec121ac424f123ca4a04) |
| 16 | On-ramp | 2026-10-23 01:21:03 | 2.5936599 | G | 65086536 | [`49b1916a…`](https://stellar.expert/explorer/public/tx/49b1916a762ce7454022e54f6affb370431741fa59a92a8dda8371f0b104e558) |
| 17 | On-ramp | 2026-10-23 01:28:13 | 5.0000000 | H | 65086622 | [`1b7d1d2b…`](https://stellar.expert/explorer/public/tx/1b7d1d2b1f360ff6e7f6626bcf6810c5c00808458e20666fa816fddc752f56f6) |
| 18 | On-ramp | 2026-10-23 01:31:33 | 5.7636888 | G | 65086662 | [`04643147…`](https://stellar.expert/explorer/public/tx/046431471729bc91c5a0e4d5cfe4e890622f54ccf8f99ee0ac5dbc4d54e145fd) |
| 19 | On-ramp | 2026-10-23 01:38:33 | 5.0000000 | H | 65086746 | [`215beb2c…`](https://stellar.expert/explorer/public/tx/215beb2ca56d647d49973a90e3f1b7255adfd26f6321f3db1323be3895feb2a6) |
| 20 | On-ramp | 2026-10-23 01:47:48 | 8.6455331 | G | 65086857 | [`1143ea4b…`](https://stellar.expert/explorer/public/tx/1143ea4b38f4302c44dfc174e1bf8dfbf53eb937e87ebc9fda2262187596ab05) |
| 21 | On-ramp | 2026-10-23 01:49:48 | 5.0000000 | H | 65086881 | [`52adabd9…`](https://stellar.expert/explorer/public/tx/52adabd9d2d738f998685dd1b526a0bfc3af67c596cfd0ccf8eebca97680a185) |
| 22 | On-ramp | 2026-10-23 16:07:38 | 4.3227666 | I | 65097175 | [`2015fe3a…`](https://stellar.expert/explorer/public/tx/2015fe3acbfe5fe6f78dc94c1dd0c84050275b4d19107a163b265ad5f11ca980) |
| 23 | On-ramp | 2026-10-23 16:23:03 | 2.8818444 | I | 65097360 | [`bb742263…`](https://stellar.expert/explorer/public/tx/bb742263ebe4ff2d44d1dde14462b30f1b3f3ab5ac63459270685bfc63794066) |
| 24 | On-ramp | 2026-10-23 16:27:53 | 2.8818444 | I | 65097418 | [`eb61a16b…`](https://stellar.expert/explorer/public/tx/eb61a16bf9f6a51df517d61cc8813d990a45508678dae65739673000550ef29c) |
| 25 | Off-ramp | 2026-10-24 02:37:58 | 1.0000000 | A | 65104739 | [`63af8460…`](https://stellar.expert/explorer/public/tx/63af8460c4aa0582f1aea9049bff427527b86c3f32285c0619f08007bafc49d2) |
| 26 | On-ramp | 2026-10-24 04:22:28 | 9.9304866 | H | 65105993 | [`6781348b…`](https://stellar.expert/explorer/public/tx/6781348b763a3186b9561f015038623b7eaaef765a66a5d1513c29caf965432e) |
| 27 | On-ramp | 2026-10-24 04:27:28 | 1000.0000000 | H | 65106053 | [`3128a641…`](https://stellar.expert/explorer/public/tx/3128a641dfe3b13cfa7a92cff0eadb3e5a1fda11d2f8eefea497c3c996018c28) |
| 28 | On-ramp | 2026-10-24 04:33:58 | 983.1181728 | H | 65106131 | [`abff9cf9…`](https://stellar.expert/explorer/public/tx/abff9cf9d08159f2040107e6a21c22e42e6b07fd3311d6e6e7efc0ee99dc00b5) |
| 29 | Off-ramp | 2026-10-27 20:38:13 | 3.0000000 | E | 65169542 | [`aa69fd94…`](https://stellar.expert/explorer/public/tx/aa69fd940e769bb9f2a23c74a8bbf8f6d096f4beaa32a49fc7740dabf8961c3d) |
| 30 | Off-ramp | 2026-10-27 21:13:38 | 3.0000000 | E | 65169967 | [`0af69270…`](https://stellar.expert/explorer/public/tx/0af69270154093947cccf818c53a863a2e66814ca00f531b529e03ae6485abf9) |

Off-ramp operations and their SEP-24 transaction (the hash memo is the transaction ID, zero-padded to 32 bytes):

| # | SEP-24 transaction | USDC | Stellar tx |
| - | ------------------ | ---: | ---------- |
| 25 | `a085c0a6-9a30-40c7-9b7f-0fb2b109395f` | 1.0000000 | [`63af8460…`](https://stellar.expert/explorer/public/tx/63af8460c4aa0582f1aea9049bff427527b86c3f32285c0619f08007bafc49d2) |
| 29 | `c924700f-222c-4420-9d76-8cc15ee867cb` | 3.0000000 | [`aa69fd94…`](https://stellar.expert/explorer/public/tx/aa69fd940e769bb9f2a23c74a8bbf8f6d096f4beaa32a49fc7740dabf8961c3d) |
| 30 | `f8ba7a25-2163-4ffa-b864-b297cf54bfdf` | 3.0000000 | [`0af69270…`](https://stellar.expert/explorer/public/tx/0af69270154093947cccf818c53a863a2e66814ca00f531b529e03ae6485abf9) |

Client accounts:

| Client | Account | Operations |
| ------ | ------- | ---------- |
| A | [`GDRDYQHWVAIILRCRXYRWYTGEE7KSLI37QE5JTWM2RWIK2NXKRRPO5Q4G`](https://stellar.expert/explorer/public/account/GDRDYQHWVAIILRCRXYRWYTGEE7KSLI37QE5JTWM2RWIK2NXKRRPO5Q4G) | 2 |
| B | [`GBJKIIFGSYTSX5T7ZWJMT22GJZMAHEYPSA6BQVXTQDOCFVQD3YQ6YEG7`](https://stellar.expert/explorer/public/account/GBJKIIFGSYTSX5T7ZWJMT22GJZMAHEYPSA6BQVXTQDOCFVQD3YQ6YEG7) | 3 |
| C | [`GDBAWV6RYVVJXCO7D5ZNJUS7HLODZBO6PN7OGXEFXMCWTTL22D2JJ2FF`](https://stellar.expert/explorer/public/account/GDBAWV6RYVVJXCO7D5ZNJUS7HLODZBO6PN7OGXEFXMCWTTL22D2JJ2FF) | 3 |
| D | [`GB7NBRUDSWFCE6PU5WKGUI4UVIALTZU5GVREVCTW6UZVZB5P4CV2OYNO`](https://stellar.expert/explorer/public/account/GB7NBRUDSWFCE6PU5WKGUI4UVIALTZU5GVREVCTW6UZVZB5P4CV2OYNO) | 3 |
| E | [`GC2272RV2K77XX7BDWULSXTFAUVN2DAKCIISJSE7PJPJEBG4TPHDKWHZ`](https://stellar.expert/explorer/public/account/GC2272RV2K77XX7BDWULSXTFAUVN2DAKCIISJSE7PJPJEBG4TPHDKWHZ) | 4 |
| F | [`GDLLOKENAHFCO62QAT3VEZN5D3E4UKENGIRIFU5REJB2SF7UYJL4OTN3`](https://stellar.expert/explorer/public/account/GDLLOKENAHFCO62QAT3VEZN5D3E4UKENGIRIFU5REJB2SF7UYJL4OTN3) | 2 |
| G | [`GDULWCMYEDU6CNQTMQYPT3PTZPK7LNNMFF2EUKAF3CYCAVHVC3DZMBJJ`](https://stellar.expert/explorer/public/account/GDULWCMYEDU6CNQTMQYPT3PTZPK7LNNMFF2EUKAF3CYCAVHVC3DZMBJJ) | 4 |
| H | [`GALCOPAETAPMBMPOZH6BKFHJCPV7VEK5KEOS2XDKQV6FILRUGNLXTKBC`](https://stellar.expert/explorer/public/account/GALCOPAETAPMBMPOZH6BKFHJCPV7VEK5KEOS2XDKQV6FILRUGNLXTKBC) | 6 |
| I | [`GAP3JWPTGTMTMYX37IMTIO6IR5BQDMO6AUBNEFHDZWZWAVOONVVUUDAT`](https://stellar.expert/explorer/public/account/GAP3JWPTGTMTMYX37IMTIO6IR5BQDMO6AUBNEFHDZWZWAVOONVVUUDAT) | 3 |

Client A is the account VERSO's team used for the first end-to-end checks on mainnet (operations 1 and 25); it went
through the same flow as every other client (SEP-10, VERSO account, KYC, real bank transfer or real USDC payment).

### Settlement time

| Direction | Measured from → to                                          | Operations | Average   | Detail                                         |
| --------- | ----------------------------------------------------------- | ---------- | --------- | ---------------------------------------------- |
| On-ramp   | Client declares the bank transfer → USDC payment on-chain   | 27         | ~5.0 min  | 26 under 15 min; slowest ~31 min (2026-10-22) |
| Off-ramp  | Client's USDC payment on-chain → fiat payout confirmed      | 3          | ~8.8 min  | ~21 min, ~0 min, ~5.4 min                     |
| **All**   |                                                             | **30**     | **~5.4 min** | Target: ≈ 30 min or less                   |

Times are taken from the admin timestamps (minute precision, ±1 min) and the on-chain payment times above. For 4
on-ramp operations the client's transfer declaration was not recorded in the webview; for those the time is measured
from the creation of the order, which can only make them look slower.

### Banks

Clients paid from **BCP, Alfin Banco and Interbank** to VERSO's CCI accounts (PEN and USD). CCI/CCE is Peru's
interbank network, so any Peruvian bank can pay into VERSO's account and receive payouts from it without a separate
integration per bank. The criterion lists BCP, BBVA and Scotiabank as examples and asks for _at least 3 different
Peruvian banks_; the three banks used are the ones VERSO's clients operated from.

### Semi-automated by design

| Step                                              | Who                                                                                     |
| ------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Live quote, CCI instructions, receipt upload      | Automatic (webview, VERSO Core rates)                                                   |
| Fiat receipt verified in VERSO's bank account, AML/compliance review | **VERSO operator** — checks the credit in the bank, then _Confirm and send_ in the admin, or _Hold_ / _Reject_ |
| USDC payout on-chain                              | Automatic (deposit worker, signed by Turnkey)                                           |
| Inbound USDC detection (off-ramp)                 | Automatic (withdrawal worker, matched by memo)                                          |
| Fiat payout by CCI/CCE                            | **VERSO operator** — _Mark paid_, refused if no on-chain USDC was detected              |
| Reconciliation of every USDC movement             | Automatic (every ~60 s, with alarms)                                                    |

Every fiat movement is approved by a person, as required by VERSO's obligations as a PSAV in Peru. Since November
2026 the admin also lets the operator hold an operation for review, reject it with a reason and register refunds
([runbook](RUNBOOK.md#5-rejections-holds-and-refunds)).

**Deviation — bank receipt verified manually.** The description mentions that a confirmed bank receipt triggers an
instant alert. It is not a measurement criterion. VERSO verifies every fiat receipt **manually in its bank
account**: when the client declares the transfer and uploads the receipt, the deposit appears in the admin queue,
the operator checks that the credit arrived in VERSO's bank account (amount, currency account, sender) and only
then confirms it. The bank is the source of truth for every fiat movement, and this manual check is how the
~5-minute average was reached.

---

## Deliverable 3 — Operational proof + Stellar Anchor Directory

| Criterion                                                      | Result                                                                                                                                        |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 10+ real on-ramp and off-ramp transactions verifiable on Stellar Expert | **30** (27 on-ramp + 3 off-ramp), each linked in the table above                                                                     |
| Mainnet account history public on Stellar Expert               | [Hot wallet history](https://stellar.expert/explorer/public/account/GCYWYXFOWGOXOLUI6ZXBSMMRRK4KJUUVHBBSPMM6XAEOAX2YU5KZNJMM): every payout and every received withdrawal is a public USDC transaction |
| VERSO submitted to the Stellar Anchor Directory                | **Deviation** — see below                                                                                                                      |

### Other movements in the hot wallet history

A reviewer reading the history will also see these movements, which are not client operations:

| Date (UTC)       | Movement                                                                                             | Explanation                                                                                                |
| ---------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| 2026-10-14       | Account created with 5 XLM; trustline to USDC                                                        | Hot wallet set-up, trustline signed by Turnkey                                                             |
| 2026-10-16       | 5.3293871 USDC in                                                                                    | First funding, before launch                                                                               |
| 2026-10-19 → 10-28 | 100, 500, 1,999 and 500 USDC in from `GC5L…Z6FH`                                                   | Top-ups from VERSO's treasury wallet                                                                       |
| 2026-10-21, 10-24, 10-28 | 0.0001 XLM in from addresses that imitate client accounts (`GDLLLR…OTN3`, `GALCUX…TKBC`, `GAL6Q6…MF7Y`) | **Address-poisoning dust.** Ignored; operators never copy addresses from the history                    |
| 2026-10-27 20:37 | 3 USDC in from client E **without memo** ([`d4c62c2b…`](https://stellar.expert/explorer/public/tx/d4c62c2b540696fb3eb919d207f068b2d3e28905b062e890b7243601a880aa79)) | Sent by the client by mistake, outside any withdrawal. Not matched to an operation (the anchor only matches by memo); the reconciliation flagged it and VERSO refunded the client |

Reconciliation runs on mainnet every ~60 s: every USDC movement of the hot wallet is ingested through Stellar RPC,
matched to its SEP-24 transaction or classified by an operator with a written note (top-ups, the payment without
memo), and the on-chain balance is compared with the internal ledger, with CloudWatch alarms on discrepancies.

### After the 30 operations

The anchor kept operating after 2026-10-27. These operations are not part of the 30 and are listed so the history
is complete:

| Date (UTC) | Type | USDC | Account | Stellar tx |
| ---------- | ---- | ---: | ------- | ---------- |
| 2026-10-28 02:45:08 | On-ramp | 496.5243297 | `GAL6EY…MF7Y` (new client) | [`c74c9a02…`](https://stellar.expert/explorer/public/tx/c74c9a0228b54f219e72fb742e2ff81128d03a68de7217539654b96e83a2e024) |
| 2026-11-07 19:01:57 | On-ramp | 4.2857143 | client E | [`d1de4044…`](https://stellar.expert/explorer/public/tx/d1de4044bfdd6bbf23e2c289f038755c4838d6905b84b2708078db14c48b3a80) |
| 2026-11-07 22:49:07 | On-ramp | 2.8571429 | `GAUPWE…G74M` (new client) | [`90a6d8b4…`](https://stellar.expert/explorer/public/tx/90a6d8b479da63a2de04304066f664aa70da9289bd6b184601747e70f425bcc0) |

### Deviation — Anchor Directory → open access from any wallet

The Stellar Anchor Directory has no public submission process, and a listing does not make an anchor available
inside wallets: each wallet integrates anchors through its own agreement. VERSO instead opened the anchor to **any
Stellar wallet**:

- it is discoverable through its `stellar.toml` by any SEP-24 wallet (SEP-1, SEP-10, SEP-24, SEP-38, all live);
- the **ANCHOR** button on [versotek.io](https://versotek.io) lets a user connect their own Stellar wallet, sign
  SEP-10 with it and run the on-ramp or off-ramp; VERSO's own clients operate through it;
- this also covers the objective _"available for ecosystem user testing upon mainnet launch"_.

---

## Deliverable 4 — Pre-launch security hardening + audit preparation

| Criterion                                                                                       | Evidence                                                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| All SEP callback endpoints reviewed: input validation confirmed                                 | Every Polaris integration point and external interface is documented with inputs, outputs, failure modes and safety rules in [`CALLBACKS.md`](CALLBACKS.md); validation is covered by the _malformed payloads_ group of the edge-case suite |
| "KMS signing verified" (Turnkey, see D1)                                                        | Each payout is built with the hot wallet as source, signed by Turnkey over the transaction hash, **verified locally against the hot wallet's public key** before submission, and saved before submitting so a retry never pays twice. Payouts above `MAX_PAYOUT_USDC` are not signed. Verified on-chain: the trustline and all 27 on-ramp payouts above are signed by the Turnkey key, the only signer of the hot wallet. Tests: `test_custody.py` (25) |
| Edge-case suite on CI: invalid SEP-10 signatures, malformed payloads, duplicate IDs, webhook replay | [`test_edge_cases.py`](../backend/verso_integrations/tests/test_edge_cases.py), **51 tests** in those four groups. Runs on every push in [GitHub Actions](../.github/workflows/backend-tests.yml) against PostgreSQL + Redis with the rest of the suite: **340 tests, all passing** |
| Internal hardening review completed, all identified issues resolved                             | **6 issues found and fixed** (below), each with a regression test                                                                                                                                                                              |

### Edge-case suite

| Group                         | What is attempted                                                                                                                                                                                                 | Verified outcome                                                                                                                                       |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Invalid SEP-10 signatures** | Challenge signed by another account, unsigned, with an extra signer, forged with another server key, expired, for another network or home domain; SEP-24 calls with no token, a forged or expired token, or another account's token | No JWT is issued (400); SEP-24 data is refused (403) and another account's transaction is not found (404)                                              |
| **Malformed payloads**        | Negative, zero, non-numeric, over-precise or non-finite amounts; incomplete payout accounts; executable, HTML or oversized receipts; unknown assets; broken JSON in SEP-38; non-UUID transaction IDs              | Validation error (400/403/404), **never a server error**, and no order is created                                                                      |
| **Duplicate IDs**             | Fiat confirmation or fiat payout applied twice; confirmation before the client declared the transfer; payout with no on-chain USDC; receipt posted twice; a second USDC payment with the memo of a paid withdrawal | The first confirmation is kept and nothing is paid twice; fiat is never released without on-chain USDC; the repeated payment raises a reconciliation discrepancy |
| **Webhook replay**            | The KYC (DIDIT) return URL replayed without a session, with a forged `status=Approved`, for another transaction or after approval; the status endpoint called without the session that opened the transaction        | KYC is never approved by the request itself: the anchor always re-checks with VERSO Core. Transaction status is only returned to its own session (403)    |

### Issues found and fixed

| #   | Issue                                                                                                   | Fix                                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1   | Non-UUID transaction IDs returned HTTP 500 on six webview endpoints                                     | They answer 400                                                                                             |
| 2   | A second USDC payment with the memo of an already-paid withdrawal was attached silently to it           | Left unmatched: it raises an `unmatched_inbound` discrepancy and an alarm                                   |
| 3   | The webview status endpoint answered anyone holding a transaction ID                                     | Only the browser session that opened the transaction gets an answer (403 otherwise)                          |
| 4   | The wallet `callback` parameter could inject script into Polaris' `more_info` page (written unescaped inside `<script>`) | Only `postMessage`, `success` or a strict `https` URL is kept; anything else is removed before rendering |
| 5   | Webview pages reachable with only a transaction ID could set or replace the transaction's `on_change_callback` | Callback URLs only come from the wallet's SEP-10-authenticated request                               |
| 6   | The client could see Polaris' internal status message (written for operators)                            | The client sees fixed texts; rejection reasons and notes stay internal                                      |

Issues 1–3 were found in the T3 hardening review (2026-10-27); 4–6 in a follow-up review in November 2026. Earlier
hardening (row locks against double payouts, safe retries, SEP-10 parsing errors answered with 400) is described in
the README.

---

## Deliverable 5 — Public technical documentation

| Criterion                                                                   | Document                                                                                                                                                                                 |
| --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SEP implementation guide covering SEP-1/10/24/38 as deployed                | [`IMPLEMENTATION_GUIDE.md`](IMPLEMENTATION_GUIDE.md) — one section per SEP, plus custody, reconciliation, history sync and how to run the tests                                          |
| Mainnet architecture diagram consistent with the Technical Architecture Document | [`ARCHITECTURE.md`](ARCHITECTURE.md) — component and sequence diagrams (Mermaid), keys and custody, trust boundaries, CCI/CCE flows. It differs from the proposal only in the documented deviations (django-polaris instead of the Anchor Platform container, Turnkey instead of AWS KMS, Stellar RPC for reconciliation) |
| Callback endpoint reference and operational runbook in the public repository | [`CALLBACKS.md`](CALLBACKS.md) (every integration hook, with request/response formats and failure modes) and [`RUNBOOK.md`](RUNBOOK.md) (operator review and approval workflow)        |

With django-polaris, the "Anchor Platform callbacks" of the proposal are integration classes that Polaris calls
in-process; `CALLBACKS.md` maps each one to its Anchor Platform equivalent.

---

## How to verify

Anyone can check the on-chain part without trusting VERSO:

```powershell
# stellar.toml and SEP endpoints
curl.exe -s https://anchor.versotek.io/.well-known/stellar.toml
curl.exe -s "https://anchor.versotek.io/auth?account=<your G... account>"
curl.exe -s https://anchor.versotek.io/sep24/info
curl.exe -s https://anchor.versotek.io/sep38/info

# USDC payments of the hot wallet (the 30 operations above, plus top-ups)
curl.exe -s "https://horizon.stellar.org/accounts/GCYWYXFOWGOXOLUI6ZXBSMMRRK4KJUUVHBBSPMM6XAEOAX2YU5KZNJMM/payments?limit=200&order=asc&join=transactions"

# SDF anchor test suite (Node.js)
npx @stellar/anchor-tests --home-domain anchor.versotek.io --seps 1 10
```

The full SEP-10 walkthrough (request, sign and submit a challenge) is in the README, _Tranche 1, Deliverable 2_; it
works the same on mainnet with `--network mainnet` and a mainnet account.
