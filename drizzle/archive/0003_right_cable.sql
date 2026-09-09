ALTER TABLE "tracking"."positions" ADD CONSTRAINT "positions_asset_id_assets_id_fk" FOREIGN KEY ("asset_id") REFERENCES "tracking"."assets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "positions_asset_time_unique" ON "tracking"."positions" USING btree ("asset_id","time");
