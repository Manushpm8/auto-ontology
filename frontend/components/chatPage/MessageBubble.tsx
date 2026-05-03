'use client';

import { useCallback, useState } from 'react';
import type { ChatMessage } from '@/types/chat';
import { FormattedContent } from './FormattedContent';

const CopyButton = ({ text }: { text: string }) => {
	const [copied, setCopied] = useState(false);

	const handleCopy = useCallback(() => {
		navigator.clipboard.writeText(text).then(() => {
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		});
	}, [text]);

	return (
		<button
			type="button"
			onClick={handleCopy}
			className="absolute top-2 right-2 rounded bg-zinc-700 px-2 py-1 text-xs text-zinc-300 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-zinc-600"
		>
			{copied ? 'Copied!' : 'Copy'}
		</button>
	);
};

type MessageBubbleProps = {
	message: ChatMessage;
};

export const MessageBubble = ({ message }: MessageBubbleProps) => {
	const isUser = message.role === 'user';

	return (
		<div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
			<div
				className={`max-w-[80%] rounded-2xl px-4 py-3 ${
					isUser
						? 'bg-emerald-600 text-white dark:bg-emerald-500'
						: 'bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100'
				}`}
			>
				{isUser ? (
					<p className="whitespace-pre-wrap text-sm leading-relaxed">
						{message.content}
					</p>
				) : (
					<FormattedContent
						content={message.content}
						className="text-sm leading-relaxed"
					/>
				)}

				{message.sql && (
					<div className="group relative mt-3 overflow-hidden rounded-lg bg-zinc-900 dark:bg-zinc-950">
						<div className="flex items-center justify-between border-b border-zinc-700 px-3 py-1.5">
							<span className="text-xs font-medium text-zinc-400">SQL</span>
							<CopyButton text={message.sql} />
						</div>
						<pre className="overflow-x-auto p-3 text-xs leading-relaxed text-emerald-400">
							<code>{message.sql}</code>
						</pre>
					</div>
				)}

				<time className="mt-1.5 block text-right text-[10px] opacity-50">
					{new Date(message.timestamp).toLocaleTimeString([], {
						hour: '2-digit',
						minute: '2-digit',
					})}
				</time>
			</div>
		</div>
	);
};
