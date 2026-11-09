/** Readable fallback for the retained Vue client, including reloaded history. */
export interface HumanQuestionText {
  prompt?: string
  options?: string[] | Record<string, string[]>
  fields?: Array<{ label: string; required?: boolean; options?: string[] }>
}

export function humanQuestionText(question: HumanQuestionText): string {
  const fields = question.fields ?? []
  const options = Array.isArray(question.options) ? question.options : Object.values(question.options ?? {}).flat()
  return [question.prompt ?? '', ...fields.map((field, index) =>
    `${index + 1}. ${field.label}${field.required ? '（必填）' : '（选填）'}${field.options?.length ? `：${field.options.join(' / ')}` : ''}`
  ), ...(!fields.length && options.length ? [`可选：${options.join(' / ')}，也可自行填写。`] : [])].filter(Boolean).join('\n')
}
