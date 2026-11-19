CREATE KEYSPACE IF NOT EXISTS fraud_data
WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

CREATE TABLE IF NOT EXISTS fraud_data.user_profiles (
    user_id text,
    event_time timestamp,
    event_type text,
    transaction_id text,
    amount double,
    location text,
    PRIMARY KEY ((user_id), event_time)
) WITH CLUSTERING ORDER BY (event_time DESC);
