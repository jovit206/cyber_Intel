CREATE CONSTRAINT threat_id_unique IF NOT EXISTS
FOR (t:Threat) REQUIRE t.threat_id IS UNIQUE;

CREATE CONSTRAINT source_key_unique IF NOT EXISTS
FOR (s:Source) REQUIRE s.source_key IS UNIQUE;

CREATE CONSTRAINT target_key_unique IF NOT EXISTS
FOR (t:Target) REQUIRE t.target_key IS UNIQUE;

CREATE CONSTRAINT threat_type_key_unique IF NOT EXISTS
FOR (t:ThreatType) REQUIRE t.type_key IS UNIQUE;

CREATE CONSTRAINT indicator_value_unique IF NOT EXISTS
FOR (i:Indicator) REQUIRE i.value IS UNIQUE;

CREATE INDEX threat_source_time IF NOT EXISTS
FOR (t:Threat) ON (t.from_source, t.timestamp);

CREATE INDEX threat_indicator_time IF NOT EXISTS
FOR (t:Threat) ON (t.indicator, t.timestamp);

CREATE INDEX threat_target_time IF NOT EXISTS
FOR (t:Threat) ON (t.target, t.timestamp);

CREATE INDEX threat_provenance_time IF NOT EXISTS
FOR (t:Threat) ON (t.provenance, t.timestamp);
