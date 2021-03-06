# sick_tree_detection
Mark a point on each small region where there are some
sick tree.

## 任务定义
可以把它看作是一个目标检测任务，而且它更简单：只需要bbox中心，不需要bbox高宽。
## 思路
###
stage-1：验证可用性
设计最简单的数据集，考察模型在训练集上的效果。  
- 使用3通道，rgb=[3,2,1]
-
总共66个目标，对每个目标，以随机offset截取一幅包含它的图像，得到66幅图像
- 90%作为训练集，10%作为验证集
-
损失函数中，控制positive/negative的权重
-
metrics函数中，仅挑选与正anchor数量一样多的负anchor，然后统计这些正负anchor上的confidence

### stage-2：人工标注
如果stage-1证明了可用性，则可以考虑这一阶段。
这一阶段基于这样的判断：有一部分（甚至是大量的）真实目标未被标注。所以要想进一步提升，必须以人工来做更多标注。
我们不是专家，我们无法根据自己的知识来做标注。但是stage-1得到的模型已经从专家标注的66个目标中学到了一些知识。  
我做如下猜想：
“模型在标注目标上的confidence较高。对其它部分，模型在大部分地方的confidence很低，但在某些位置的confidence也会较高，这些位置可能就是**未标注的目标**。”
我们可以对stage-1模型的预测结果卡一个阈值，认为高于这个阈值的位置很可能是目标（可能已经被专家标注了，也可能是未标注的）。然后对这些**可能目标**做人工筛选。这样可以再添加上一部分标注。
然后根据这些更多的标注再生成数据集，训练，观察结果是否有进步。
如果这个方法是可行的，则可以迭代多次：训练-->预测-->人工筛选-->更多数据-->训练-->预测-->...

### stage-3：使用4通道
如果stage-2是可行的，则可以试验使用4通道数据，看是否比3通道时有进步。  
为什么不在stage-1就用4通道？
我认为，3通道跟4通道比，如果不是表现一样，也不会差很多，而且使用起来更简单。

### stage-？


## tif转图像
### tif文件：
遥感影像原文件为tif文件，其为4通道影像
- ch0: Blue
- ch1: Green
- ch2: Red
- ch3: 红外

### 如何使用原数据
对原tif数据的使用可以有2种方式：
#### 转换为3通道
##### 两种转换方式：
- rgb=[2,1,0]，是真彩色，所见与真实颜色
-
rgb=[3,2,1]，假彩色，植被特征明显

##### 优缺点：
- 优点：
    - 可视化，有助于人工调试
    - 方便套用到常见的图像任务中
-
缺点：丢掉一个通道的信息


#### 保留4通道
##### 优缺点：
- 优点：保留全部信息
- 缺点：
    - 不能可视化
    -
在构造databunch，使用预训练模型时可能需要额外的工作

## 建立数据集
建立数据集就是从整幅非常大（30000*40000个像素）的遥感影像中截取出大量的小尺寸图片构成训练集的过程。

有以下几点考虑：
### 纯黑区域
在遥感影像中有连续的像素为0的区域，这里叫它纯黑区域。
在截取小尺寸图片时，要注意排除纯黑区域超过一定比例（例如90%）的情况。
### 截取包含目标的图片
在整幅影像中，目标非常少，总共66个。如果在整幅图像中随机截取，则包含目标的概率很低。可以换一种方式：

- 假设要截取的尺寸为h*w
-
随机选择一个目标，假设它的坐标为 x0,y0
- 随机产生两个小数0<a,b<1
-
则截取区域的x范围为[x0-ah,x0+(1-a)h],类似的，y的范围为[y0-bw,y0+(1-b)w]

### 直接从tif图像中截取
可以直接从tif图像中截取，截取后要把像素值转换到[0,255]范围。
如果可以的话，保存为jpg格式是比较方便的。但4通道图像可以保存为jpg吗？png呢？
如果4通道图像只能保存为tif，在创建databunch时需要额外工作吗？

## 构造databunch

### normalization
通常在用预训练模型时，要使用预训练模型的数据集（通常是ImageNet）的mean和std。但是，遥感图像跟ImageNet有不同，尤其是其红外通道，在ImageNet上就没有。所以应该在我们自己的数据集上统计mean和std。
可以在整幅原始图像上统计，但要注意排除**纯黑区域**的影响。

## 模型

### 在预训练模型上用4通道数据
预训练模型都是针对3通道图像训练的，当用4通道数据时，要做一定调整。
只需要调整第一个卷积层，例如resnet，其第一个卷积层是conv(ks=7,inch=3,outch=64)，要改为conv(ks=7,**inch=4**,outch=64)。并且这个卷积层无法用预训练的参数，只能随机初始化。
