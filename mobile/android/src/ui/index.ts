// The Wash component kit for Android (spec §5.2, guide §9). Screens import
// UI primitives from here only.

export { Pressable, fireHaptic, type PressableProps, type Haptic } from './Pressable';
export { Text, textStyle, type TextProps, type TextVariant, type TextTone } from './Text';
export { Icon, type IconName, type IconProps } from './Icon';
export { Button, IconButton, resolveVariant, type ButtonProps, type ButtonVariant } from './Button';
export { Chip, StatusChip, StatusIcon, StatusPill, Pill, Eyebrow, CountBadge, type ChipProps } from './Chip';
export { Card, Panel, StatStrip, StatTile, KV, Pre, Empty, Loading, type CardProps, type StatItem } from './Card';
export { Tabs, Segmented, type TabItem } from './Tabs';
export { Field, TextField, Select, ChoiceRow, SwitchRow, Switch, CheckRow, Checkbox, Slider, type TextFieldProps } from './Form';
export { Sheet, Dialog, ModalFrame, type SheetProps, type DialogProps } from './Sheet';
export { ToastHost, useToast, Banner, EmptyState, ErrorState, type BannerProps, type EmptyStateProps } from './Feedback';
export { ToolCard, ToolGroup, groupToolCalls, toolKind, toolKindLabel, toolKindIcon, primaryArg, groupSummary, type ToolCallView, type ToolKind } from './ToolCard';
export { ApprovalCard, PromptFrame, ChoicePrompt, type ApprovalCardProps, type PromptQueue, type ChoiceOption } from './ApprovalCard';
export { DiffView, DiffLines, DiffFileHeader, looksLikeDiff } from './DiffView';
export { CodeBlock } from '../components/CodeBlock';
export { Markdown, markdownStyles, markdownRules, type MarkdownVariant } from './Markdown';
export { InkDrop, ProgressBar, Skeleton, ResultBar, Meter, Sparkline, type SkeletonProps } from './Progress';
export { PipelineStepper, MiniStepper, describeStep, type StepLike } from './Stepper';
export { ContextRing, LayerBar, contextTone, layerTone, CONTEXT_CAUTION, CONTEXT_DANGER, type LayerSegment } from './Meters';
export { WashBackground, washArt, spotWash, type WashArt } from './WashBackground';
export { glassStyle, useGlass, stateTint, activePillStyle, GlassChrome, GlassBackdrop, Stage, AmbientBackground, AmbientLayer, ambientArt, routeAmbient, useAmbientOverride, GLASS_MIN_ALPHA, type GlassLevel, type AmbientArt } from './Glass';
export { layout, useInterval } from './layout';
export type { Tone, Semantic } from '../theme/tokens';
