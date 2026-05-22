from bson import ObjectId

def to_json(data):
    """MongoDB 데이터를 JSON 호환 형식으로 변환"""
    if isinstance(data, list):
        return [to_json(item) for item in data]
    if isinstance(data, dict):
        return {
            key: to_json(value) for key, value in data.items()
        }
    if isinstance(data, ObjectId):
        return str(data)
    return data
