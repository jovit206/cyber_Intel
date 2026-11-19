const status = rs.status();
const expectedHosts = ["mongo1:27017", "mongo2:27017", "mongo3:27017"];
const members = status.members.map((member) => ({
  name: member.name,
  state: member.stateStr,
  health: member.health,
}));

printjson({ set: status.set, members });

const validMembers =
  status.set === "rs0" &&
  members.length === 3 &&
  members.every((member) => expectedHosts.includes(member.name) && member.health === 1) &&
  members.filter((member) => member.state === "PRIMARY").length === 1 &&
  members.filter((member) => member.state === "SECONDARY").length === 2;

if (!validMembers) {
  throw new Error("Replica set is not healthy: expected one PRIMARY and two SECONDARY members.");
}
