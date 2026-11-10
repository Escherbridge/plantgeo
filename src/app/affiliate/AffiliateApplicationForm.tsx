"use client";

import { useState } from "react";
import { editorialFocusRing, editorialFocusRingInverse } from "@/components/ui/editorial";
import { cn } from "@/lib/utils";

const ROLES = [
  { value: "", label: "Role (optional)" },
  { value: "designer", label: "Designer" },
  { value: "extension", label: "Extension" },
  { value: "installer", label: "Installer" },
  { value: "school-garden", label: "School garden" },
  { value: "other", label: "Other" },
] as const;

const fieldClass =
  "editorial-flat rule-all-medium w-full border-rule bg-paper px-snug py-tight font-editorial-text text-body text-ink";

export function AffiliateApplicationForm() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [organization, setOrganization] = useState("");
  const [role, setRole] = useState("");
  const [website, setWebsite] = useState("");
  const [message, setMessage] = useState("");
  const [faxNumber, setFaxNumber] = useState("");
  const [pending, setPending] = useState(false);
  const [received, setReceived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (received) {
    return (
      <p role="status" className="max-w-measure font-editorial-text text-body text-ink">
        Application received. Terms will be shared on review.
      </p>
    );
  }

  return (
    <form
      className="flex max-w-measure flex-col gap-comfortable"
      onSubmit={(event) => {
        event.preventDefault();
        setError(null);
        setPending(true);
        void fetch("/api/affiliate/apply", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            name,
            email,
            organization,
            role,
            website,
            message,
            fax_number: faxNumber,
          }),
        })
          .then(async (response) => {
            if (response.status === 200 || response.status === 204) {
              setReceived(true);
              return;
            }
            const payload = (await response.json().catch(() => null)) as { error?: string } | null;
            setError(payload?.error ?? "The application could not be saved.");
          })
          .catch(() => {
            setError("The application could not be saved.");
          })
          .finally(() => {
            setPending(false);
          });
      }}
    >
      <div className="flex flex-col gap-tight">
        <label htmlFor="affiliate-name" className="font-editorial-label text-label text-ink-muted uppercase">
          Name
        </label>
        <input id="affiliate-name" required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} className={cn(fieldClass, editorialFocusRing)} />
      </div>
      <div className="flex flex-col gap-tight">
        <label htmlFor="affiliate-email" className="font-editorial-label text-label text-ink-muted uppercase">
          Email
        </label>
        <input id="affiliate-email" type="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} className={cn(fieldClass, editorialFocusRing)} />
      </div>
      <div className="flex flex-col gap-tight">
        <label htmlFor="affiliate-organization" className="font-editorial-label text-label text-ink-muted uppercase">
          Organization
        </label>
        <input id="affiliate-organization" maxLength={200} value={organization} onChange={(event) => setOrganization(event.target.value)} className={cn(fieldClass, editorialFocusRing)} />
      </div>
      <div className="flex flex-col gap-tight">
        <label htmlFor="affiliate-role" className="font-editorial-label text-label text-ink-muted uppercase">
          Role
        </label>
        <select id="affiliate-role" value={role} onChange={(event) => setRole(event.target.value)} className={cn(fieldClass, editorialFocusRing)}>
          {ROLES.map((option) => (
            <option key={option.value || "unset"} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-tight">
        <label htmlFor="affiliate-website" className="font-editorial-label text-label text-ink-muted uppercase">
          Website
        </label>
        <input id="affiliate-website" type="url" maxLength={500} value={website} onChange={(event) => setWebsite(event.target.value)} className={cn(fieldClass, editorialFocusRing)} />
      </div>
      <div className="flex flex-col gap-tight">
        <label htmlFor="affiliate-message" className="font-editorial-label text-label text-ink-muted uppercase">
          Message
        </label>
        <textarea id="affiliate-message" maxLength={4000} rows={5} value={message} onChange={(event) => setMessage(event.target.value)} className={cn(fieldClass, editorialFocusRing)} />
      </div>
      <div aria-hidden="true" className="absolute left-[-9999px] h-px w-px overflow-hidden">
        <label htmlFor="affiliate-fax">
          Fax
          <input
            id="affiliate-fax"
            name="fax_number"
            tabIndex={-1}
            autoComplete="off"
            value={faxNumber}
            onChange={(event) => setFaxNumber(event.target.value)}
          />
        </label>
      </div>
      <button
        type="submit"
        disabled={pending}
        className={cn(
          "bg-ink px-comfortable py-snug font-editorial-label text-label text-paper uppercase hover:bg-accent hover:text-accent-ink disabled:opacity-70",
          editorialFocusRingInverse
        )}
      >
        {pending ? "Sending" : "Submit application"}
      </button>
      {error ? (
        <p role="alert" className="font-editorial-text text-caption text-ink">
          {error}
        </p>
      ) : null}
    </form>
  );
}
