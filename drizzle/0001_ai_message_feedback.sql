CREATE TABLE public.ai_message_feedback (
    message_id uuid NOT NULL REFERENCES public.ai_messages(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    rating varchar(16) NOT NULL,
    reason varchar(1000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, user_id),
    CONSTRAINT ai_message_feedback_rating_check CHECK (rating IN ('helpful', 'not_helpful'))
);
--> statement-breakpoint
CREATE INDEX ai_message_feedback_user_idx ON public.ai_message_feedback (user_id);
