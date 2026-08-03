"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { ArrowLeft, ArrowRight, Loader2, X } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { toast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { OrganizationTypePicker, type OrganizationType } from "./OrganizationTypePicker";

const inputClass =
  "rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500";

type Step = "basics" | "type" | "details";
const STEPS: Step[] = ["basics", "type", "details"];
const STEP_LABEL: Record<Step, string> = {
  basics: "Name",
  type: "Type",
  details: "Details",
};

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

interface CreateOrganizationFormProps {
  onCancel: () => void;
}

/** Three-step wizard: name/slug, organization type, then optional details. */
export function CreateOrganizationForm({ onCancel }: CreateOrganizationFormProps) {
  const router = useRouter();
  const { update: updateSession } = useSession();
  const utils = trpc.useUtils();

  const [step, setStep] = useState<Step>("basics");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [orgType, setOrgType] = useState<OrganizationType | undefined>(undefined);
  const [description, setDescription] = useState("");
  const [website, setWebsite] = useState("");
  const [specialties, setSpecialties] = useState<string[]>([]);
  const [specialtyDraft, setSpecialtyDraft] = useState("");

  const effectiveSlug = slugTouched ? slug : slugify(name);
  const stepIndex = STEPS.indexOf(step);

  const createMutation = trpc.teams.createTeam.useMutation();
  const setActiveMutation = trpc.teams.setActiveTeam.useMutation();

  function addSpecialty() {
    const value = specialtyDraft.trim();
    if (!value) return;
    if (!specialties.includes(value)) setSpecialties((prev) => [...prev, value]);
    setSpecialtyDraft("");
  }

  function handleSpecialtyKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addSpecialty();
    } else if (e.key === "Backspace" && specialtyDraft === "" && specialties.length > 0) {
      setSpecialties((prev) => prev.slice(0, -1));
    }
  }

  function normalizedWebsite(): string | undefined {
    const trimmed = website.trim();
    if (!trimmed) return undefined;
    return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  }

  async function handleCreate() {
    if (!name.trim() || !orgType) return;
    try {
      const team = await createMutation.mutateAsync({
        name: name.trim(),
        slug: effectiveSlug || undefined,
        orgType,
        description: description.trim() || undefined,
        website: normalizedWebsite(),
        specialties: specialties.length ? specialties : undefined,
      });
      await setActiveMutation.mutateAsync({ teamId: team.id });
      await updateSession({ activeTeamId: team.id });
      await utils.teams.listMyTeams.invalidate();
      toast.success(`${team.name} is ready`, { description: "You're set as the owner." });
      router.push("/dashboard/org");
    } catch {
      // Surfaced inline below via createMutation.error / setActiveMutation.error.
    }
  }

  const submitting = createMutation.isPending || setActiveMutation.isPending;
  const mutationError = createMutation.error?.message ?? setActiveMutation.error?.message;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-onboarding-display)]">
          Create your organization
        </h2>
        <p className="mt-1 text-xs text-zinc-500">
          Only a name and type are required &mdash; everything else can wait.
        </p>
      </div>

      <div className="flex items-center gap-2" aria-hidden>
        {STEPS.map((s, i) => (
          <div key={s} className="flex flex-1 items-center gap-2">
            <div
              className={cn(
                "h-1 flex-1 rounded-full transition-colors",
                i <= stepIndex ? "bg-emerald-500" : "bg-zinc-800"
              )}
            />
          </div>
        ))}
      </div>
      <div className="-mt-4 flex justify-between text-[10px] uppercase tracking-[0.2em] text-zinc-600">
        {STEPS.map((s) => (
          <span key={s} className={s === step ? "text-emerald-500" : undefined}>
            {STEP_LABEL[s]}
          </span>
        ))}
      </div>

      {step === "basics" && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-sm text-zinc-300" htmlFor="org-name">
              Organization name
            </label>
            <input
              id="org-name"
              type="text"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Cascade Watershed Alliance"
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <label className="text-sm text-zinc-300" htmlFor="org-slug">
                URL slug
              </label>
              {!slugTouched && effectiveSlug && (
                <button
                  type="button"
                  onClick={() => {
                    setSlug(effectiveSlug);
                    setSlugTouched(true);
                  }}
                  className="text-xs text-emerald-400 transition-colors hover:text-emerald-300"
                >
                  Customize
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-zinc-600">plantgeo.app/org/</span>
              <input
                id="org-slug"
                type="text"
                value={effectiveSlug}
                onChange={(e) => {
                  setSlugTouched(true);
                  setSlug(slugify(e.target.value));
                }}
                readOnly={!slugTouched}
                placeholder="cascade-watershed-alliance"
                className={cn(inputClass, "flex-1", !slugTouched && "cursor-default text-zinc-400")}
              />
            </div>
            <p className="text-[11px] text-zinc-600">
              Final availability is confirmed when you create the organization.
            </p>
          </div>
        </div>
      )}

      {step === "type" && (
        <OrganizationTypePicker value={orgType} onChange={setOrgType} />
      )}

      {step === "details" && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-sm text-zinc-300" htmlFor="org-description">
              Description <span className="text-zinc-600">(optional)</span>
            </label>
            <textarea
              id="org-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this organization work on?"
              className={cn(inputClass, "resize-none")}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm text-zinc-300" htmlFor="org-website">
              Website <span className="text-zinc-600">(optional)</span>
            </label>
            <input
              id="org-website"
              type="text"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              placeholder="example.org"
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm text-zinc-300" htmlFor="org-specialties">
              Specialties <span className="text-zinc-600">(optional)</span>
            </label>
            <input
              id="org-specialties"
              type="text"
              value={specialtyDraft}
              onChange={(e) => setSpecialtyDraft(e.target.value)}
              onKeyDown={handleSpecialtyKeyDown}
              onBlur={addSpecialty}
              placeholder="Press Enter to add: wildfire, reforestation…"
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
        </div>
      )}

      {mutationError && <p className="text-sm text-red-400">{mutationError}</p>}

      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => (stepIndex === 0 ? onCancel() : setStep(STEPS[stepIndex - 1]))}
          className="flex items-center gap-1 rounded-md px-3 py-2 text-sm text-zinc-400 transition-colors hover:text-zinc-200"
        >
          <ArrowLeft className="h-4 w-4" />
          {stepIndex === 0 ? "Back" : "Previous"}
        </button>

        {step !== "details" ? (
          <button
            type="button"
            disabled={step === "basics" ? !name.trim() : !orgType}
            onClick={() => setStep(STEPS[stepIndex + 1])}
            className="flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
          >
            Continue
            <ArrowRight className="h-4 w-4" />
          </button>
        ) : (
          <button
            type="button"
            disabled={submitting || !name.trim() || !orgType}
            onClick={handleCreate}
            className="flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitting ? "Creating…" : "Create organization"}
          </button>
        )}
      </div>
    </div>
  );
}
