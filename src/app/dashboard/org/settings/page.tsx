"use client";

import { useState } from "react";
import { Loader2, Save, X } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { toast } from "@/components/ui/toast";
import { OrganizationTypePicker, type OrganizationType } from "@/components/onboarding/OrganizationTypePicker";
import { useActiveOrganization } from "../useActiveOrganization";

type OrganizationRow = NonNullable<ReturnType<typeof useActiveOrganization>["organization"]>;

const inputClass =
  "rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500";

/** Organization settings: profile fields for owners/members, slug editing restricted to owners. */
export default function OrganizationSettingsPage() {
  const { organization, role, isLoading } = useActiveOrganization();

  if (isLoading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
        <p className="text-sm text-zinc-400">Loading settings…</p>
      </div>
    );
  }

  if (!organization) {
    return <p className="py-16 text-center text-sm text-zinc-500">No organization to configure.</p>;
  }

  if (role === "viewer") {
    return (
      <p className="py-16 text-center text-sm text-zinc-500">
        Viewers can&rsquo;t edit organization settings. Ask an owner or member for changes.
      </p>
    );
  }

  return <SettingsForm organization={organization} isOwner={role === "owner"} />;
}

function SettingsForm({ organization, isOwner }: { organization: OrganizationRow; isOwner: boolean }) {
  const utils = trpc.useUtils();

  const [name, setName] = useState(organization.name);
  const [slug, setSlug] = useState(organization.slug ?? "");
  const [description, setDescription] = useState(organization.description ?? "");
  const [orgType, setOrgType] = useState<OrganizationType | undefined>(
    organization.orgType as OrganizationType | undefined
  );
  const [website, setWebsite] = useState(organization.website ?? "");
  const [specialties, setSpecialties] = useState<string[]>(organization.specialties ?? []);
  const [specialtyDraft, setSpecialtyDraft] = useState("");

  const updateMutation = trpc.teams.updateTeam.useMutation({
    onSuccess: () => {
      toast.success("Organization updated");
      utils.teams.listMyTeams.invalidate();
    },
    onError: (err) => {
      toast.error("Couldn't save changes", { description: err.message });
    },
  });

  function addSpecialty() {
    const value = specialtyDraft.trim();
    if (!value) return;
    if (!specialties.includes(value)) setSpecialties((prev) => [...prev, value]);
    setSpecialtyDraft("");
  }

  function normalizedWebsite(): string | undefined {
    const trimmed = website.trim();
    if (!trimmed) return undefined;
    return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  }

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    updateMutation.mutate({
      id: organization.id,
      name: name.trim(),
      slug: isOwner ? slug.trim() || undefined : undefined,
      description: description.trim() || undefined,
      orgType,
      website: normalizedWebsite() ?? null,
      specialties,
    });
  }

  return (
    <form onSubmit={handleSave} className="flex flex-col gap-6">
      <h1 className="text-lg text-zinc-100 [font-family:var(--font-onboarding-display,inherit)]">
        Organization settings
      </h1>

      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="settings-name">
          Name
        </label>
        <input
          id="settings-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className={inputClass}
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="settings-slug">
          URL slug
        </label>
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-600">plantgeo.app/org/</span>
          <input
            id="settings-slug"
            value={slug}
            disabled={!isOwner}
            onChange={(e) => setSlug(e.target.value.toLowerCase())}
            className={`${inputClass} flex-1 disabled:cursor-not-allowed disabled:opacity-50`}
          />
        </div>
        {!isOwner && <p className="text-[11px] text-zinc-600">Only owners can change the slug.</p>}
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="settings-description">
          Description
        </label>
        <textarea
          id="settings-description"
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className={`${inputClass} resize-none`}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-sm text-zinc-300">Organization type</span>
        <OrganizationTypePicker value={orgType} onChange={setOrgType} />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="settings-website">
          Website
        </label>
        <input
          id="settings-website"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
          className={inputClass}
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="settings-specialties">
          Specialties
        </label>
        <input
          id="settings-specialties"
          value={specialtyDraft}
          onChange={(e) => setSpecialtyDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              addSpecialty();
            } else if (e.key === "Backspace" && specialtyDraft === "" && specialties.length > 0) {
              setSpecialties((prev) => prev.slice(0, -1));
            }
          }}
          onBlur={addSpecialty}
          placeholder="Press Enter to add"
          className={inputClass}
        />
        {specialties.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {specialties.map((tag) => (
              <span
                key={tag}
                className="flex items-center gap-1 rounded-full border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300"
              >
                {tag}
                <button
                  type="button"
                  onClick={() => setSpecialties((prev) => prev.filter((t) => t !== tag))}
                  className="text-zinc-500 transition-colors hover:text-red-400"
                  aria-label={`Remove ${tag}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <button
        type="submit"
        disabled={updateMutation.isPending || !name.trim()}
        className="flex items-center justify-center gap-2 self-start rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
      >
        {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save changes
      </button>
    </form>
  );
}
