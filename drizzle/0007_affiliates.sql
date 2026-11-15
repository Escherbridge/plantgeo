-- Additive affiliate records issued when an application is approved.
-- No DROP, no ALTER of an existing table, no seed, and no payout columns.
-- A code is active or inactive. A plan keeps a code only while it is active.
-- Nothing here records a rate, a payout, or a payout date.

CREATE TABLE "affiliates" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"application_id" uuid NOT NULL,
	"email" text NOT NULL,
	"name" text NOT NULL,
	"organization" text,
	"code" text NOT NULL,
	"status" text DEFAULT 'active' NOT NULL,
	"user_id" uuid,
	"approved_by" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "affiliates_application_id_unique" UNIQUE("application_id"),
	CONSTRAINT "affiliates_code_unique" UNIQUE("code"),
	CONSTRAINT "affiliates_code_shape" CHECK ("code" ~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$'),
	CONSTRAINT "affiliates_status_known" CHECK ("status" IN ('active', 'inactive'))
);
--> statement-breakpoint
ALTER TABLE "affiliates" ADD CONSTRAINT "affiliates_application_id_affiliate_applications_id_fk" FOREIGN KEY ("application_id") REFERENCES "public"."affiliate_applications"("id") ON DELETE no action ON UPDATE no action;
--> statement-breakpoint
ALTER TABLE "affiliates" ADD CONSTRAINT "affiliates_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;
--> statement-breakpoint
ALTER TABLE "affiliates" ADD CONSTRAINT "affiliates_approved_by_users_id_fk" FOREIGN KEY ("approved_by") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;
--> statement-breakpoint
CREATE UNIQUE INDEX "affiliates_active_email_unique" ON "affiliates" USING btree ("email") WHERE "status" = 'active';
