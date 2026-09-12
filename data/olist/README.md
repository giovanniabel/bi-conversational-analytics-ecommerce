# data/olist/

This directory holds the raw Olist dataset CSVs used to populate
ClickHouse. It's empty in git — populate it by running, from the repo
root:

```bash
./scripts/download_dataset.sh
```

That downloads the dataset via the official Kaggle API (using your own
Kaggle account credentials) rather than this repo redistributing a copy
of the data itself.

## Dataset & attribution

**Brazilian E-Commerce Public Dataset by Olist**
Source: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
License: **CC BY-NC-SA 4.0** (Attribution, NonCommercial, ShareAlike)

This license applies to the *data* in this directory — it is independent
of, and not affected by, whatever license the application code in this
repository carries. Use of this data is restricted to non-commercial
purposes, requires attribution to Olist, and any redistribution of the
data (or derivatives of it) must be under the same license.
