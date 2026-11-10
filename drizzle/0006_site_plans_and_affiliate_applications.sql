-- Additive site plans and affiliate applications.
-- No DROP, no ALTER of existing tables, and no user seed.
-- The inserted row is a demonstration camera over Ada County, Idaho.
-- It is not a customer, a parcel claim, or a geo.features write.

CREATE TABLE "site_plans" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"public_id" text NOT NULL,
	"title" text NOT NULL,
	"description" text,
	"is_sample" boolean DEFAULT false NOT NULL,
	"sample_label" text,
	"bbox" jsonb NOT NULL,
	"viewport" jsonb,
	"geometry" jsonb,
	"ref_code" text,
	"owner_user_id" uuid,
	"guest_token_hash" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "site_plans_public_id_unique" UNIQUE("public_id"),
	CONSTRAINT "site_plans_guest_token_hash_unique" UNIQUE("guest_token_hash")
);
--> statement-breakpoint
ALTER TABLE "site_plans" ADD CONSTRAINT "site_plans_owner_user_id_users_id_fk" FOREIGN KEY ("owner_user_id") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;
--> statement-breakpoint
CREATE INDEX "site_plans_ref_code_idx" ON "site_plans" USING btree ("ref_code");
--> statement-breakpoint
CREATE UNIQUE INDEX "site_plans_one_sample" ON "site_plans" ((true)) WHERE "is_sample";
--> statement-breakpoint
CREATE TABLE "site_plan_items" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"plan_id" uuid NOT NULL,
	"aevani_product_id" text NOT NULL,
	"catalog_slug" text,
	"title" text NOT NULL,
	"quantity" integer DEFAULT 1 NOT NULL,
	"notes" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "site_plan_items_plan_product_unique" UNIQUE("plan_id","aevani_product_id"),
	CONSTRAINT "site_plan_items_quantity_positive" CHECK ("quantity" >= 1)
);
--> statement-breakpoint
ALTER TABLE "site_plan_items" ADD CONSTRAINT "site_plan_items_plan_id_site_plans_id_fk" FOREIGN KEY ("plan_id") REFERENCES "public"."site_plans"("id") ON DELETE cascade ON UPDATE no action;
--> statement-breakpoint
CREATE TABLE "affiliate_applications" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"email" text NOT NULL,
	"organization" text,
	"role" text,
	"website" text,
	"message" text,
	"status" text DEFAULT 'pending' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX "affiliate_applications_email_idx" ON "affiliate_applications" USING btree ("email");
--> statement-breakpoint
CREATE INDEX "affiliate_applications_created_idx" ON "affiliate_applications" USING btree ("created_at");
--> statement-breakpoint
INSERT INTO "site_plans" (
	"public_id",
	"title",
	"description",
	"is_sample",
	"sample_label",
	"bbox",
	"viewport",
	"ref_code"
) VALUES (
	'sample-ada-county',
	'Sample plan',
	'A demonstration site plan on real Ada County, Idaho data. Not a real customer.',
	true,
	'Sample plan — not a real customer',
	'{"west":-116.70,"south":43.14,"east":-115.99,"north":43.81}'::jsonb,
	NULL,
	'ada-demo'
) ON CONFLICT ("public_id") DO NOTHING;
