export default async function teardown() {
  await fetch("http://127.0.0.1:3308/__community_acceptance_shutdown", {
    method: "POST",
    signal: AbortSignal.timeout(15_000),
  }).catch((error: unknown) => console.warn("Community acceptance fixture cleanup was unavailable:", error));
}
