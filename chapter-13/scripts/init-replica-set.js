const expectedHosts = ["mongo1:27017", "mongo2:27017", "mongo3:27017"];
let status;

try {
  status = rs.status();
} catch (error) {
  if (error.code !== 94) {
    throw error;
  }
}

if (!status) {
  rs.initiate({
    _id: "rs0",
    members: expectedHosts.map((host, index) => ({ _id: index, host })),
  });
  print("Replica set rs0 initiation requested.");
} else {
  if (status.set !== "rs0") {
    throw new Error(`Expected replica set rs0, found ${status.set}.`);
  }
  const configuredHosts = rs.conf().members.map((member) => member.host).sort();
  if (JSON.stringify(configuredHosts) !== JSON.stringify([...expectedHosts].sort())) {
    throw new Error(`Existing rs0 members differ from this lab configuration: ${configuredHosts.join(", ")}`);
  }
  print("Replica set rs0 is already configured with the expected members.");
}
