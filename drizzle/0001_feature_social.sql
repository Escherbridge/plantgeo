CREATE TABLE "geo"."feature_likes" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"feature_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "uq_feature_likes_feature_user" UNIQUE("feature_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "geo"."feature_comments" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"feature_id" uuid NOT NULL,
	"author_user_id" uuid NOT NULL,
	"body" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"deleted_at" timestamp with time zone,
	"deleted_by_user_id" uuid
);
--> statement-breakpoint
ALTER TABLE "geo"."feature_likes" ADD CONSTRAINT "feature_likes_feature_id_features_id_fk" FOREIGN KEY ("feature_id") REFERENCES "geo"."features"("id") ON DELETE cascade ON UPDATE no action;
--> statement-breakpoint
ALTER TABLE "geo"."feature_likes" ADD CONSTRAINT "feature_likes_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;
--> statement-breakpoint
ALTER TABLE "geo"."feature_comments" ADD CONSTRAINT "feature_comments_feature_id_features_id_fk" FOREIGN KEY ("feature_id") REFERENCES "geo"."features"("id") ON DELETE cascade ON UPDATE no action;
--> statement-breakpoint
ALTER TABLE "geo"."feature_comments" ADD CONSTRAINT "feature_comments_author_user_id_users_id_fk" FOREIGN KEY ("author_user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;
--> statement-breakpoint
ALTER TABLE "geo"."feature_comments" ADD CONSTRAINT "feature_comments_deleted_by_user_id_users_id_fk" FOREIGN KEY ("deleted_by_user_id") REFERENCES "public"."users"("id") ON DELETE set null ON UPDATE no action;
--> statement-breakpoint
CREATE INDEX "ix_feature_likes_feature" ON "geo"."feature_likes" USING btree ("feature_id");
--> statement-breakpoint
CREATE INDEX "ix_feature_comments_feature_created" ON "geo"."feature_comments" USING btree ("feature_id","created_at") WHERE "geo"."feature_comments"."deleted_at" IS NULL;
