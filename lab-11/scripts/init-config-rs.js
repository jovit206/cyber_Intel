const config = {
  _id: "c11-config-rs",
  configsvr: true,
  members: [
    { _id: 0, host: "c11-config1:27019" },
    { _id: 1, host: "c11-config2:27019" },
    { _id: 2, host: "c11-config3:27019" }
  ]
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
