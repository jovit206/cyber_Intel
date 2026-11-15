const config = {
  _id: "c11-shard2-rs",
  members: [{ _id: 0, host: "c11-shard2:27018" }]
};

try {
  const current = rs.status();
  if (current.set !== config._id) throw new Error(`Unexpected replica set ${current.set}`);
  print(`Already initialized: ${config._id}`);
} catch (error) {
  if (error.code === 94 || error.codeName === "NotYetInitialized") {
    printjson(rs.initiate(config));
  } else {
    throw error;
  }
}
