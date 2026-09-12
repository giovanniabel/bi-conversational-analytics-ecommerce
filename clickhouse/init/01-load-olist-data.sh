#!/bin/bash
# Runs automatically via ClickHouse's docker-entrypoint-initdb.d mechanism —
# ONLY on a brand-new (empty) data volume, i.e. the first time this stack
# is started. On an existing volume with data already loaded, ClickHouse
# skips docker-entrypoint-initdb.d entirely, so this never touches or
# reloads data on a machine that's already running.
set -e

CH_USER="${CLICKHOUSE_USER:-admin}"
CH_PASSWORD="${CLICKHOUSE_PASSWORD:-admin123}"
DATA_DIR="/olist-data"

CH=(clickhouse-client --host 127.0.0.1 --port 9000 -u "$CH_USER" --password "$CH_PASSWORD")

REQUIRED_FILES=(
    olist_customers_dataset.csv
    olist_geolocation_dataset.csv
    olist_order_items_dataset.csv
    olist_order_payments_dataset.csv
    olist_order_reviews_dataset.csv
    olist_orders_dataset.csv
    olist_products_dataset.csv
    olist_sellers_dataset.csv
    product_category_name_translation.csv
)

missing=0
for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$DATA_DIR/$f" ]; then
        echo "[olist-init] missing $DATA_DIR/$f" >&2
        missing=1
    fi
done

if [ "$missing" -eq 1 ]; then
    cat >&2 <<'EOF'
[olist-init] Dataset CSVs not found in data/olist/ on the host.

Run this first, then restart the stack:

    ./scripts/download_dataset.sh
    docker compose down -v   # only needed if ClickHouse already partially initialized
    docker compose up -d
EOF
    exit 1
fi

echo "=== [olist-init] Creating database and tables ==="

"${CH[@]}" --query "CREATE DATABASE IF NOT EXISTS olist"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.customers (
    customer_id String,
    customer_unique_id String,
    customer_zip_code_prefix String,
    customer_city String,
    customer_state String
) ENGINE = MergeTree()
ORDER BY customer_id
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.geolocation (
    geolocation_zip_code_prefix String,
    geolocation_lat Float64,
    geolocation_lng Float64,
    geolocation_city String,
    geolocation_state String
) ENGINE = MergeTree()
ORDER BY geolocation_zip_code_prefix
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.order_items (
    order_id String,
    order_item_id UInt32,
    product_id String,
    seller_id String,
    shipping_limit_date DateTime,
    price Float64,
    freight_value Float64
) ENGINE = MergeTree()
ORDER BY (order_id, order_item_id)
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.order_payments (
    order_id String,
    payment_sequential UInt32,
    payment_type String,
    payment_installments UInt32,
    payment_value Float64
) ENGINE = MergeTree()
ORDER BY (order_id, payment_sequential)
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.order_reviews (
    review_id String,
    order_id String,
    review_score UInt8,
    review_comment_title Nullable(String),
    review_comment_message Nullable(String),
    review_creation_date DateTime,
    review_answer_timestamp DateTime
) ENGINE = MergeTree()
ORDER BY (order_id, review_id)
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.orders (
    order_id String,
    customer_id String,
    order_status String,
    order_purchase_timestamp DateTime,
    order_approved_at Nullable(DateTime),
    order_delivered_carrier_date Nullable(DateTime),
    order_delivered_customer_date Nullable(DateTime),
    order_estimated_delivery_date DateTime
) ENGINE = MergeTree()
ORDER BY (order_id)
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.products (
    product_id String,
    product_category_name Nullable(String),
    product_name_lenght Nullable(UInt32),
    product_description_lenght Nullable(UInt32),
    product_photos_qty Nullable(UInt32),
    product_weight_g Nullable(UInt32),
    product_length_cm Nullable(UInt32),
    product_height_cm Nullable(UInt32),
    product_width_cm Nullable(UInt32)
) ENGINE = MergeTree()
ORDER BY product_id
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.sellers (
    seller_id String,
    seller_zip_code_prefix String,
    seller_city String,
    seller_state String
) ENGINE = MergeTree()
ORDER BY seller_id
"

"${CH[@]}" --query "
CREATE TABLE IF NOT EXISTS olist.product_category_name_translation (
    product_category_name String,
    product_category_name_english String
) ENGINE = MergeTree()
ORDER BY product_category_name
"

echo "=== [olist-init] Loading data ==="

echo "[olist-init] customers..."
"${CH[@]}" --query "INSERT INTO olist.customers FORMAT CSVWithNames" < "$DATA_DIR/olist_customers_dataset.csv"

echo "[olist-init] geolocation..."
"${CH[@]}" --query "INSERT INTO olist.geolocation FORMAT CSVWithNames" < "$DATA_DIR/olist_geolocation_dataset.csv"

echo "[olist-init] order_items..."
"${CH[@]}" --query "INSERT INTO olist.order_items FORMAT CSVWithNames" < "$DATA_DIR/olist_order_items_dataset.csv"

echo "[olist-init] order_payments..."
"${CH[@]}" --query "INSERT INTO olist.order_payments FORMAT CSVWithNames" < "$DATA_DIR/olist_order_payments_dataset.csv"

echo "[olist-init] order_reviews..."
"${CH[@]}" --query "INSERT INTO olist.order_reviews SETTINGS input_format_csv_allow_cr_end_of_line=1 FORMAT CSVWithNames" < "$DATA_DIR/olist_order_reviews_dataset.csv"

echo "[olist-init] orders..."
"${CH[@]}" --query "INSERT INTO olist.orders FORMAT CSVWithNames" < "$DATA_DIR/olist_orders_dataset.csv"

echo "[olist-init] products..."
"${CH[@]}" --query "INSERT INTO olist.products FORMAT CSVWithNames" < "$DATA_DIR/olist_products_dataset.csv"

echo "[olist-init] sellers..."
"${CH[@]}" --query "INSERT INTO olist.sellers FORMAT CSVWithNames" < "$DATA_DIR/olist_sellers_dataset.csv"

echo "[olist-init] product_category_name_translation..."
"${CH[@]}" --query "INSERT INTO olist.product_category_name_translation SETTINGS input_format_csv_allow_cr_end_of_line=1 FORMAT CSVWithNames" < "$DATA_DIR/product_category_name_translation.csv"

echo "=== [olist-init] Row counts ==="
for table in customers geolocation order_items order_payments order_reviews orders products sellers product_category_name_translation; do
    count=$("${CH[@]}" --query "SELECT count() FROM olist.$table")
    echo "  olist.$table: $count rows"
done

echo "=== [olist-init] Done ==="
