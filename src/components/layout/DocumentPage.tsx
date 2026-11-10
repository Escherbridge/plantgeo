import type { ReactNode } from "react";
import {
  EditorialContainer,
  EditorialDisplay,
  EditorialEyebrow,
  EditorialGrid,
  EditorialLead,
  EditorialPage,
} from "@/components/ui/editorial";

/** Shared masthead for the public document routes (contact, privacy, terms). */
export function DocumentPage({
  eyebrow,
  title,
  lead,
  children,
}: {
  eyebrow: string;
  title: string;
  lead: ReactNode;
  children: ReactNode;
}) {
  return (
    <EditorialPage>
      <EditorialContainer>
        <header className="pt-section pb-roomy">
          <EditorialGrid>
            <div className="col-span-4 md:col-span-8">
              <EditorialEyebrow>{eyebrow}</EditorialEyebrow>
              <EditorialDisplay className="mt-comfortable">{title}</EditorialDisplay>
            </div>
          </EditorialGrid>
          <EditorialGrid className="mt-roomy">
            <div className="col-span-4 md:col-span-7 md:col-start-5">
              <EditorialLead>{lead}</EditorialLead>
            </div>
          </EditorialGrid>
        </header>
        {children}
      </EditorialContainer>
    </EditorialPage>
  );
}
