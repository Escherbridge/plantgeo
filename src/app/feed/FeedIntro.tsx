"use client";

import { useSession } from "next-auth/react";
import { EditorialLead } from "@/components/ui/editorial";

const SIGNED_OUT =
  "You'll see proposals awaiting review here once you sign in. Anyone with an account can read them.";

/**
 * Signed-out visitors never receive the proposal list, so the intro must not
 * promise entries below it. Signed-in readers still get the list description.
 */
export function FeedIntro() {
  const { status } = useSession();
  if (status === "authenticated") {
    return (
      <EditorialLead>
        Every entry below is a recommendation a contributor put on the ground and asked to have
        published — reforestation, silvopasture, cover cropping, biochar, keyline earthworks. None
        of them is on the public map yet; each is waiting for an expert to publish or reject it.
        Follow any entry to see the site it names.
      </EditorialLead>
    );
  }
  return <EditorialLead>{SIGNED_OUT}</EditorialLead>;
}
