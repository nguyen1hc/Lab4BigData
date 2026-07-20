from __future__ import annotations

import os

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, from_json
from pyspark.sql.types import (
    ArrayType,
    IntegerType,
    MapType,
    StringType,
    StructField,
    StructType,
)


metadata_schema = StructType(
    [
        StructField("_id", StringType(), False),
        StructField("repo_id", StringType(), False),
        StructField("repo_url", StringType(), True),
        StructField("path", StringType(), False),
        StructField("content_hash", StringType(), False),
        StructField("size_bytes", IntegerType(), False),
        StructField("line_count", IntegerType(), False),
        StructField("commit_sha", StringType(), True),
        StructField("parse_status", StringType(), False),
        StructField("node_counts", MapType(StringType(), IntegerType()), True),
        StructField("edge_counts", MapType(StringType(), IntegerType()), True),
        StructField("warnings", ArrayType(StringType()), True),
        StructField("error_message", StringType(), True),
        StructField("processed_at", StringType(), False),
        StructField("run_id", StringType(), False),
        StructField("schema_version", StringType(), False),
    ]
)

event_schema = StructType(
    [
        StructField("schema_version", StringType(), False),
        StructField("event_time", StringType(), False),
        StructField("repo_id", StringType(), False),
        StructField("file_id", StringType(), False),
        StructField("run_id", StringType(), False),
        StructField("content_hash", StringType(), False),
        StructField("event_id", StringType(), False),
        StructField("op", StringType(), False),
        StructField("metadata", metadata_schema, False),
    ]
)


def main() -> None:
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "broker:29092")
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://mongo:27017")
    database = os.getenv("MONGODB_DATABASE", "lab04")
    collection = os.getenv("MONGODB_COLLECTION", "source_metadata")
    checkpoint = os.getenv("CHECKPOINT_DIR", "/opt/checkpoints/source-metadata-v1")

    spark = (
        SparkSession.builder.appName("lab04-source-metadata-to-mongo")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    kafka = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", "cpg.source-metadata.v1")
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "true")
        .option("kafka.isolation.level", "read_committed")
        .load()
    )

    parsed = kafka.select(
        from_json(col("value").cast("string"), event_schema).alias("event"),
        col("topic").alias("kafka_topic"),
        col("partition").alias("kafka_partition"),
        col("offset").alias("kafka_offset"),
        col("timestamp").cast("string").alias("kafka_timestamp"),
    )
    documents = parsed.select(
        "event.metadata.*", "kafka_topic", "kafka_partition", "kafka_offset", "kafka_timestamp"
    ).where(col("_id").isNotNull())

    query = (
        documents.writeStream.format("mongodb")
        .queryName("lab04-source-metadata-to-mongo")
        .option("checkpointLocation", checkpoint)
        .option("spark.mongodb.connection.uri", mongo_uri)
        .option("spark.mongodb.database", database)
        .option("spark.mongodb.collection", collection)
        .option("operationType", "replace")
        .option("idFieldList", "_id")
        .option("upsertDocument", "true")
        .outputMode("append")
        .trigger(processingTime="5 seconds")
        .start()
    )
    query.awaitTermination()


if __name__ == "__main__":
    main()

