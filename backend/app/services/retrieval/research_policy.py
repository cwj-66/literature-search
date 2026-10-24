"""Research-reading policy shared by retrieval planning, generation and review."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Intent = Literal['reading','motivation','method','experiments','critique','comparison','exploration']
Mode = Literal['understand','compare','challenge','explore']
Focus = Literal['motivation','prior_work','method','experiment','limitation','claim','definition']
Category = Literal['author_claim','experimental_result','analysis','hypothesis']
LABELS={'author_claim':'作者陈述','experimental_result':'实验支持的结论','analysis':'助手分析','hypothesis':'待验证研究设想'}
FOCUS={
    'reading':['definition','claim'], 'motivation':['motivation','prior_work'],
    'method':['method','motivation','claim'], 'experiments':['experiment','claim','method'],
    'critique':['claim','experiment','method','limitation'],
    'comparison':['method','claim','experiment','limitation'],
    'exploration':['method','limitation','experiment'],
}
MODES={'comparison':'compare','critique':'challenge','exploration':'explore'}


def hint(question, papers):
    """A deterministic fallback; the normal planner reasons about the full request."""
    checks=[('exploration',r'值得研究|研究方向|延伸研究|研究设想|如何结合|怎么结合|能否结合|可否结合|未来工作|后续研究|组合.*方法|research direction|future work|combine|hypothes'),
            ('comparison',r'比较|对比|共同|共性|差异|异同|观点冲突|compare|difference|common|shared|conflicting views'),
            ('critique',r'批判|质疑|可靠吗|可信|可靠性|局限|潜在问题|站得住脚|有说服力|缺陷|漏洞|证据是否|足以支持|结论.*成立|critique|limitation|reliab|validity'),
            ('experiments',r'实验|消融|结果|参数|验证观点|证明什么|experiment|ablation|result'),
            ('motivation',r'研究动机|为什么研究|为何研究|为什么.*问题|现有方法.*不足|motivation|prior work'),
            ('method',r'如何|怎么|机制|具体.*做|设计|创新|步骤|method|mechanis|design')]
    for intent,pattern in checks:
        if re.search(pattern,question,re.I) and (intent!='comparison' or papers>1):return intent
    return 'reading'


class ResearchStatement(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text: str = Field(min_length=1,max_length=2000)
    category: Category
    evidence_ids: list[str] = Field(min_length=1,max_length=48)
    reasoning: str = Field(default='',max_length=2000)
    conditions: str = Field(default='',max_length=1200)
    uncertainty: str = Field(default='',max_length=1200)
    validation: str = Field(default='',max_length=1600)

    @model_validator(mode='after')
    def grounded_reasoning(self):
        if self.category in ('analysis','hypothesis') and (not self.reasoning.strip() or not self.uncertainty.strip()):
            raise ValueError('Analysis/proposals require an explicit reasoning premise and uncertainty')
        if self.category=='hypothesis' and not self.validation.strip():
            raise ValueError('Research proposals require a validation plan, not a claimed result')
        if self.category=='experimental_result' and not self.conditions.strip():
            raise ValueError('Experimental conclusions require their evaluated conditions')
        return self


GENERATION_POLICY=(
    '你是论文研究助手。论文是研究证据来源，不是真理；目标是帮助用户理解研究、分析方法、审视证据、比较观点和形成判断。'
    '按研究意图组织回答，不强求唯一标准答案，也不为了完整而编造内容。'
    '在内部claims中区分四类内容：作者陈述、实验支持的结论、助手分析、待验证研究设想。分类服务于核验，不是正文模板。'
    '作者动机、贡献和结论应归因于作者，不能仅因论文这样写就断言客观成立。'
    '实验结果须限定数据集、基线、指标和已检验条件，不能把相关性、有限实验或自报结果升级为普遍或因果证明。'
    '架构设计、训练配方、损失掩码和超参数设置本身属于作者的方法陈述，不是experimental_result；该类别只用于观察到的评估结果及其限定结论。'
    '优先表述为作者在指定评估条件下报告或实验支持，不笼统声称论文证明了普遍真理。'
    '允许批判和合理推理：先列出可引用的事实前提，再说明推理链、适用条件、反例或不确定性；不要求推理结论在原文出现。'
    '区分作者明确承认的局限与助手基于方法、假设或实验范围提出的潜在局限，后者不可写成作者已经承认。'
    '当前检索片段没出现某实验，不等于作者全文没做过它；应说当前证据未显示，不能未经证据断言作者从未验证。'
    '可以提出方法结合和新研究问题，但必须标为待验证设想，说明来源前提、可能失败的条件及如何设计对照或消融验证；不能写成已经提升或必然有效。'
    '多篇观点可以不一致；逐篇归属后，检查任务、假设、数据和指标是否可比，区分真实冲突与条件差异，不强行调和。'
    '共性允许基于各篇证据作合理归纳，但不得偷换概念；不能因为排序质量与知识冲突相关就断言各篇都研究知识冲突。'
    '作者内部私下想法、未报告的数值和实验结果不能推断为事实。资料不足时说明边界，可提出有条件的分析或验证计划。'
    '若追问指向上轮或未选论文，先说明当前范围并建议重新选择目标论文，不得用当前论文的相似概念替代那个模块作答。'
    '正文先直接回答用户的问题，再按问题需要展开。用连贯自然的中文、适量段落和必要的小标题，避免逐条罗列原文片段。'
    '简短问题通常用一到三段即可；复杂问题使用少量问题导向的小标题，不按每条证据另起层级，不重复已经解释过的背景。'
    '用可识别的论文短名或给定标题指代论文，不用裸arxiv/doi编号充当段落标题。'
    '理解方法时按机制或流程解释；比较时按统一维度组织，必要时用Markdown表格；推荐对比阅读时按论文组合说明，不要写成逐篇摘录。'
    '不要在正文反复显示【作者陈述】【实验支持的结论】【助手分析】【待验证研究设想】，不要逐项展开适用条件/推理依据/不确定性/验证方式的固定表单。'
    '用自然措辞表达边界，例如作者报告、在该实验设置下、据此可以推测但尚未验证；将必要的条件和不确定性融入相应论述。'
    '仅在提出新设想或批判性推断时明确提醒这是分析或待验证建议，不要求每句话都加类别标签；推理依据不能省略，但应融入解释而非机械重复。'
    '每个关键论述附证据ID；分析和设想的引用证明其前提，不表示原文已经证明该分析或新方案。'
    'answer中出现的助手分析和研究设想，也必须在claims中提供相应类别的独立论述、前提和边界，不可只分类作者陈述。'
)

REVIEW_POLICY=(
    '这是研究辅助复核，supported表示陈述忠实或推理在标注的边界内有依据，不表示论文结论是客观真理。'
    '按category分别审核：author_claim核对作者是否这样陈述及归属，不能把作者声称升级为已证实；'
    'experimental_result核对实验条件、数据、指标和结论范围，拒绝越界泛化或把相关性当因果；'
    '训练设置、损失掩码或架构说明不是观测到的实验结果，若误标为experimental_result则拒绝该分类；'
    'analysis核对引用前提是否真实、推理是否合理、是否标明为助手分析和保留不确定性，不因论文未明确说出该推论就拒绝；'
    'hypothesis核对其方法/局限前提、条件表达、不确定性和验证计划，不要求论文已经实施或证实该新设想，'
    '但将设想写成已验证事实、编造提升数值或作者真实想法必须拒绝。'
    '片段中未见实验不等于全文不存在实验；无直接否定证据时，拒绝把作者没有做过某实验写成已知事实。'
    '整份混合回答按各类规则逐项审核；开放式研究无唯一答案、论文间存在矛盾本身不是拒绝理由。'
    '跨论文共性可为标明的合理归纳，前提必须逐篇支持同一概念；间接联想不能充当某篇研究同一具体问题的证据。'
    '若某句不支持，不影响其他已支持的作者陈述、限定结论、合理分析或待验证设想单独保留。'
    '自然语言归因和边界表达有效，不要求正文包含固定类别名称或方括号标签。审核真实语义，不按是否出现固定标签决定通过。'
    '整份回答中的实质性分析和设想须有对应分类论述和依据，不能因为文字流畅就放过未分类的推断或把设想写成事实。'
    '阅读顺序、对比维度等研究建议没有唯一标准答案；只要前提逐篇有依据、归因为助手建议且保留不确定性，就可判supported。'
    'verdict必须与note一致：若判insufficient/contradictory/unverifiable，note必须指出具体未支持的句子或缺失前提；若各组成论述均支持、整体无额外未支持主张且边界清楚，整份回答应判supported，不能一边说明均已通过一边给insufficient。'
)
