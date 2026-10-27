"""
Agent A (Role 3): ML lead scoring for the MCP-native agentic CRM.

    features   the model's input contract (shared by training and live scoring)
    dataset    Kaggle "Leads X Education" -> canonical CRM lead records
    predictor  LeadScorer: lead record -> {score, bucket, top_factors, model_version}
    crm        CRM clients: MCP (primary), REST (fallback)
    agent      the polling agent that scores unscored leads and writes back
    service    optional HTTP prediction service
    training   EDA, training, evaluation, model artifacts
"""

__version__ = "1.0.0"
