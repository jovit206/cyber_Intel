const username = process.env.MONGO_EXPORTER_USERNAME;
const password = process.env.MONGO_EXPORTER_PASSWORD;
const rootUsername = process.env.MONGO_INITDB_ROOT_USERNAME;
const rootPassword = process.env.MONGO_INITDB_ROOT_PASSWORD;

if (!username || !password || !rootUsername || !rootPassword) {
  throw new Error("MongoDB administrator and exporter credentials must be set.");
}

const primary = rs.status().members.find((member) => member.stateStr === "PRIMARY");
if (!primary) {
  throw new Error("Cannot create the monitoring user before a PRIMARY is elected.");
}

const uri =
  `mongodb://${encodeURIComponent(rootUsername)}:${encodeURIComponent(rootPassword)}` +
  `@${primary.name}/admin?authSource=admin`;
const primaryAdmin = new Mongo(uri).getDB("admin");

if (!primaryAdmin.getUser(username)) {
  primaryAdmin.createUser({
    user: username,
    pwd: password,
    roles: [{ role: "clusterMonitor", db: "admin" }],
  });
  print(`Created least-privilege monitoring user '${username}'.`);
} else {
  primaryAdmin.updateUser(username, {
    pwd: password,
    roles: [{ role: "clusterMonitor", db: "admin" }],
  });
  print(`Updated least-privilege monitoring user '${username}'.`);
}
