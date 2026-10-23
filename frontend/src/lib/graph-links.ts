/** Return each actual outcome so failed recommendations remain available for retry. */
export async function connectSuggestions<T>(items: readonly T[], connect: (item: T) => Promise<boolean>) {
  const successes: T[] = [];
  const failures: T[] = [];
  for (const item of items) {
    try { (await connect(item) ? successes : failures).push(item); }
    catch { failures.push(item); }
  }
  return { successes, failures };
}
