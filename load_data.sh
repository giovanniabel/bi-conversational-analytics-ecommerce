#!/bin/bash
# Manual/alternative loader: (re)loads the Olist dataset into an
# ALREADY-RUNNING clickhouse-local container via `docker exec`. Useful if
# you need to reload data without recreating the container/volume — the
# automatic path (clickhouse/init/01-load-olist-data.sh, wired up via
# docker-compose.yaml) only runs on a container's first-ever start.
#
# Expects data/olist/ to already be populated — run ./scripts/download_dataset.sh
# first if it's empty.
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CH="docker exec -i clickhouse-local clickhouse-client --user admin --password admin123"
DATA_DIR="$REPO_ROOT/data/olist"

if [ ! -f "$DATA_DIR/olist_customers_dataset.csv" ]; then
    echo "error: $DATA_DIR is empty. Run ./scripts/download_dataset.sh first." >&2
    exit 1
fi

echo "=== Creating database olist ==="
$CH --query "CREATE DATABASE IF NOT EXISTS olist"

echo "=== Creating tables ==="

$CH --query "
CREATE TABLE IF NOT EXISTS olist.customers (
    customer_id String,
    customer_unique_id String,
    customer_zip_code_prefix String,
    customer_city String,
    customer_state String
) ENGINE = MergeTree()
ORDER BY customer_id
"

$CH --query "
CREATE TABLE IF NOT EXISTS olist.geolocation (
    geolocation_zip_code_prefix String,
    geolocation_lat Float64,
    geolocation_lng Float64,
    geolocation_city String,
    geolocation_state String
) ENGINE = MergeTree()
ORDER BY geolocation_zip_code_prefix
"

$CH --query "
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

$CH --query "
CREATE TABLE IF NOT EXISTS olist.order_payments (
    order_id String,
    payment_sequential UInt32,
    payment_type String,
    payment_installments UInt32,
    payment_value Float64
) ENGINE = MergeTree()
ORDER BY (order_id, payment_sequential)
"

$CH --query "
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

$CH --query "
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

$CH --query "
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

$CH --query "
CREATE TABLE IF NOT EXISTS olist.sellers (
    seller_id String,
    seller_zip_code_prefix String,
    seller_city String,
    seller_state String
) ENGINE = MergeTree()
ORDER BY seller_id
"

$CH --query "
CREATE TABLE IF NOT EXISTS olist.product_category_name_translation (
    product_category_name String,
    product_category_name_english String
) ENGINE = MergeTree()
ORDER BY product_category_name
"

echo "=== Loading data ==="

echo "Loading customers..."
cat "$DATA_DIR/olist_customers_dataset.csv" | $CH --query "INSERT INTO olist.customers FORMAT CSVWithNames"

echo "Loading geolocation..."
cat "$DATA_DIR/olist_geolocation_dataset.csv" | $CH --query "INSERT INTO olist.geolocation FORMAT CSVWithNames"

echo "Loading order_items..."
cat "$DATA_DIR/olist_order_items_dataset.csv" | $CH --query "INSERT INTO olist.order_items FORMAT CSVWithNames"

echo "Loading order_payments..."
cat "$DATA_DIR/olist_order_payments_dataset.csv" | $CH --query "INSERT INTO olist.order_payments FORMAT CSVWithNames"

echo "Loading order_reviews..."
cat "$DATA_DIR/olist_order_reviews_dataset.csv" | $CH --query "INSERT INTO olist.order_reviews SETTINGS input_format_csv_allow_cr_end_of_line=1 FORMAT CSVWithNames"

echo "Loading orders..."
cat "$DATA_DIR/olist_orders_dataset.csv" | $CH --query "INSERT INTO olist.orders FORMAT CSVWithNames"

echo "Loading products..."
cat "$DATA_DIR/olist_products_dataset.csv" | $CH --query "INSERT INTO olist.products FORMAT CSVWithNames"

echo "Loading sellers..."
cat "$DATA_DIR/olist_sellers_dataset.csv" | $CH --query "INSERT INTO olist.sellers FORMAT CSVWithNames"

echo "Loading product_category_name_translation..."
cat "$DATA_DIR/product_category_name_translation.csv" | $CH --query "INSERT INTO olist.product_category_name_translation SETTINGS input_format_csv_allow_cr_end_of_line=1 FORMAT CSVWithNames"

echo ""
echo "=== Verifying row counts ==="
for table in customers geolocation order_items order_payments order_reviews orders products sellers product_category_name_translation; do
    count=$($CH --query "SELECT count() FROM olist.$table")
    echo "  olist.$table: $count rows"
done

echo ""
echo "=== Done! All data loaded into ClickHouse ==="
