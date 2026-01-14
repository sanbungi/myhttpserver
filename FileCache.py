class FileCache:
    def __init__(self):
        self.cache = {}
        self.hits = 0
        self.misses = 0
    
    def read(self, filepath, mode='r'):
        cache_key = (filepath, mode)
        
        if cache_key in self.cache:
            self.hits += 1
            return self.cache[cache_key]
        
        self.misses += 1
        
        if mode == 'r':
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        else:
            with open(filepath, 'rb') as f:
                content = f.read()
        
        self.cache[cache_key] = content
        return content
    
    def stats(self):
        return {'hits': self.hits, 'misses': self.misses}
    
    def clear(self):
        self.cache.clear()