import type { Bundle } from "@/components/Decision/model";

import { VotesList } from "../VotesList";

export function Votes({ bundle, now }: { bundle: Bundle; now: number }) {
  return (
    <div className="max-w-3xl">
      <VotesList decision={bundle.decision} votes={bundle.votes} now={now} />
    </div>
  );
}
