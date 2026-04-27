import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import { NavRail } from '@/components/NavRail';
import { AppTopBar } from '@/components/AppTopBar';
import { BreadcrumbProvider } from '@/contexts/BreadcrumbContext';
import './globals.css';

const geistSans = Geist({
	variable: '--font-geist-sans',
	subsets: ['latin'],
});

const geistMono = Geist_Mono({
	variable: '--font-geist-mono',
	subsets: ['latin'],
});

export const metadata: Metadata = {
	title: {
		default: 'GSF — NVIDIA',
		template: 'GSF - %s',
	},
	description: 'NVIDIA GSF — Generative Semantic Fabric',
	icons: {
		icon: '/favicon.svg',
	},
};

export default function RootLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<html
			lang="en"
			className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
		>
			<body className="flex h-full flex-col bg-white text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
				<BreadcrumbProvider>
					<AppTopBar />
					<div className="flex min-h-0 flex-1">
						<NavRail />
						<div className="min-w-0 flex-1">{children}</div>
					</div>
				</BreadcrumbProvider>
			</body>
		</html>
	);
}
