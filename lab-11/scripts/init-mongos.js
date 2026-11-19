const listed = db.adminCommand({ listShards: 1 });
if (listed.ok !== 1) throw new Error(`listShards failed: ${tojson(listed)}`);

if ((listed.shards || []).length === 0) {
  const concern = db.adminCommand({
    setDefaultRWConcern: 1,
    defaultWriteConcern: { w: "majority" }
  });
  if (concern.ok !== 1) throw new Error(`Could not set majority default write concern: ${tojson(concern)}`);
}

const requested = [
  { name: "c11-shard1-rs", connection: "c11-shard1-rs/c11-shard1:27018" },
  { name: "c11-shard2-rs", connection: "c11-shard2-rs/c11-shard2:27018" }
];
for (const shard of requested) {
  const current = db.adminCommand({ listShards: 1 });
  if ((current.shards || []).some(item => item._id === shard.name)) continue;
  const result = db.adminCommand({ addShard: shard.connection, name: shard.name });
  if (result.ok !== 1) throw new Error(`addShard ${shard.name} failed: ${tojson(result)}`);
  printjson(result);
}

const enabled = db.adminCommand({ enableSharding: "chapter11" });
if (enabled.ok !== 1) throw new Error(`enableSharding failed: ${tojson(enabled)}`);
printjson(db.adminCommand({ listShards: 1 }));
