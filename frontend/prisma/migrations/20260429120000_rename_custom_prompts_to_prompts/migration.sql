-- RenameTable
ALTER TABLE "custom_prompts" RENAME TO "prompts";

-- RenameConstraint
ALTER TABLE "prompts" RENAME CONSTRAINT "custom_prompts_pkey" TO "prompts_pkey";
